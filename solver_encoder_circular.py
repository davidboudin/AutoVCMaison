from model_vc import Generator
import torch
import torch.nn.functional as F
import time
import datetime
import os
from make_metadata import load_speaker_embedding_model

from torch_utils import device

class Solver(object):

    def __init__(self, vcc_loader, config):
        """Initialize configurations."""

        # Data loader.
        self.vcc_loader = vcc_loader

        # Model configurations.
        self.lambda_cd = config.lambda_cd
        self.dim_neck = config.dim_neck
        self.dim_emb = config.dim_emb
        self.dim_pre = config.dim_pre
        self.freq = config.freq
        self.init_model = config.init_model
        self.init_iter = 0
        self.loss = []
        self.speaker_embedder = load_speaker_embedding_model()

        # Training configurations.
        self.batch_size = config.batch_size
        self.num_iters = config.num_iters
        self.autosave = config.checkpoint_mode=='autosave'
        self.saving_pace = config.save_every_n_iter
        self.saving_prefix = config.save_path
        self.learning_rate = config.learning_rate
        self.use_speaker_loss = config.use_speaker_loss

        # Miscellaneous.
        self.device = device
        self.log_step = config.log_step

        # Build the model and tensorboard.
        self.build_model()

        if self.init_model:
            self.load_trainable_model(self.init_model)




    def build_model(self):

        self.G = Generator(self.dim_neck, self.dim_emb, self.dim_pre, self.freq)

        self.g_optimizer = torch.optim.Adam(self.G.parameters(), self.learning_rate)

        self.G.to(self.device)


    def save_model(self, path = 'autovc.ckpt'):
        torch.save({
            'G_state_dict': self.G.state_dict(),
            'hyperparams':{'dim_neck': self.dim_neck, 'dim_emb': self.dim_emb, 'dim_pre': self.dim_pre, 'freq': self.freq}
            }, path)
        print("model state dict saved at ",path)


    def load_model(self, path = 'autovc.ckpt'):
        if os.path.exists(path):
            print("Load weights from" + path + "for inference")
            self.G.load_state_dict(torch.load(path))
            self.G.eval()
        else:
            print("No checkpoint found, starting from scratch")



    def load_trainable_model(self, path):
        if os.path.exists(self.init_model):
            try:
                print(f'Loading model : {self.init_model}...')
                checkpoint = torch.load(self.init_model)
                self.G.load_state_dict(checkpoint['G_state_dict'])
                self.g_optimizer.load_state_dict(checkpoint['g_optimizer_state_dict'])
                self.loss = checkpoint["G_loss"]
                self.init_iter = len(self.loss)
                del checkpoint
            except:
                raise Exception(f'Could not load model at {self.init_model}.')
        else:
            raise Exception(f'Incorrect path: {self.init_model}')

    def save_trainable_model(self, path):
        torch.save({
            'hyperparams':{'dim_neck': self.dim_neck, 'dim_emb': self.dim_emb, 'dim_pre': self.dim_pre, 'freq': self.freq},
            'G_state_dict': self.G.state_dict(),
            'g_optimizer_state_dict': self.g_optimizer.state_dict(),
            'G_loss': self.loss
            }, path)


    def reset_grad(self):
        """Reset the gradient buffers."""
        self.g_optimizer.zero_grad()


    #=====================================================================================================================================#

    def train(self):
        # Set data loader.
        data_loader = self.vcc_loader

        # Print logs in specified order
        keys = ['G/loss', 'G/loss_id','G/loss_id_psnt','G/loss_cd']
        if self.use_speaker_loss:
            keys.append('G/loss_tgt_style')

        # Start training.
        print('Start training...')
        try:
            start_time = time.time()
            loss = {}
            for i in range(self.init_iter, self.init_iter + self.num_iters):

                # =================================================================================== #
                #                             1. Preprocess input data                                #
                # =================================================================================== #

                # Fetch data.
                try:
                    x_real, emb_org, emb_target = next(data_iter)
                except:
                    data_iter = iter(data_loader)
                    x_real, emb_org, emb_target = next(data_iter)

                x_real = x_real.to(self.device)

                emb_org = emb_org.to(self.device)

                emb_target = emb_target.to(self.device)


                # =================================================================================== #
                #                               2. Train the generator                                #
                # =================================================================================== #

                self.G = self.G.train()

                # Circular mapping loss
                x_target_pred, x_target_pred_psnt, code_org = self.G(x_real, emb_org, emb_target)
                x_org_reconst, x_org_reconst_psnt, code_target_pred = self.G(x_target_pred.reshape(x_real.shape), emb_target, emb_org)
                x_real_reshaped = x_real.reshape((x_real.shape[0],1,x_real.shape[1],x_real.shape[2]))
                g_loss_id = F.l1_loss(x_real_reshaped, x_org_reconst)
                g_loss_id_psnt = F.l1_loss(x_real_reshaped, x_org_reconst_psnt)

                # Code semantic loss.
                g_loss_cd = F.l1_loss(code_org, code_target_pred)

                # Output style domain loss

                g_loss_target_style = torch.Tensor([0]).to(self.device)
                if self.use_speaker_loss:
                    emb_target_pred = self.speaker_embedder(x_target_pred_psnt.reshape(x_real.shape)).to(self.device)
                    g_loss_target_style = F.l1_loss(emb_target_pred, emb_target)


                del x_real, x_real_reshaped, emb_org, x_org_reconst, x_org_reconst_psnt, x_target_pred, x_target_pred_psnt, code_org


                # Backward and optimize.
                g_loss = g_loss_id + g_loss_id_psnt + g_loss_target_style + self.lambda_cd * g_loss_cd

                # Logging.
                loss['G/loss'] = g_loss.item() + loss.get('G/loss', 0)
                loss['G/loss_id'] = g_loss_id.item() + loss.get('G/loss_id', 0)
                loss['G/loss_id_psnt'] = g_loss_id_psnt.item() + loss.get('G/loss_id_psnt', 0)
                loss['G/loss_cd'] = g_loss_cd.item() + loss.get('G/loss_cd', 0)
                loss['G/loss_tgt_style'] = g_loss_target_style.item() + loss.get('G/loss_tgt_style', 0)

                del g_loss_id, g_loss_id_psnt, g_loss_cd, g_loss_target_style

                self.loss.append(g_loss.item())
                self.reset_grad()
                g_loss.backward()
                self.g_optimizer.step()



                # =================================================================================== #
                #                                 4. Miscellaneous                                    #
                # =================================================================================== #

                # Print out training information.
                if (i+1) % self.log_step == 0:
                    et = time.time() - start_time
                    et = str(datetime.timedelta(seconds=et))[:-7]
                    log = "Elapsed [{}], Iteration [{}/{}]".format(et, i+1, self.num_iters)
                    for tag in keys:
                        log += ", {}: {:.4f}".format(tag, loss[tag]/self.log_step)
                    print(log)
                    loss = {}

                if self.saving_pace!=0 and (i+1) % self.saving_pace == 0:
                    if not os.path.exists('./trained_models'):
                        os.mkdir('trained_models')
                    self.save_trainable_model(f'./trained_models/autovc_{self.saving_prefix}_{i+1}')
        except KeyboardInterrupt:
            if self.autosave:
                self.save_trainable_model('autovc_autosave.ckpt')
                raise Exception('KeyboardInterrupt: autosave done.')
            raise Exception('KeyboardInterrupt: no autosave.')
