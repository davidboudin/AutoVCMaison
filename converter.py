import argparse
import os
import pickle
import torch
import numpy as np
from math import ceil
from model_vc import Generator
from torch_utils import device
import librosa
from synthesis import build_model
from synthesis import wavegen
import soundfile as sf
from torch_utils import device


def pad_seq(x, base=32):
    len_out = int(base * ceil(float(x.shape[0])/base))
    len_pad = len_out - x.shape[0]
    assert len_pad >= 0
    return np.pad(x, ((0,len_pad),(0,0)), 'constant'), len_pad

def get_embedding(metadata, speaker):
    for sbmt_i in metadata:
        if sbmt_i[0] == speaker:
            return torch.from_numpy(sbmt_i[1][np.newaxis, :]).to(device)
    raise Exception(f'Embedding was not found for speaker {speaker}.')
    #TODO : generate embedding from David's functions

def get_uttr_melspect(uttr_wav_path, spmelFolder):
    uttr_spmel_path = os.path.join(spmelFolder,uttr_wav_path[:-4]+'.npy')
    mel_spect_exists = os.path.isfile(uttr_spmel_path)
    if mel_spect_exists:
        mlspect = np.load(uttr_spmel_path)
    else:
        alter_suffix = os.path.join(uttr_spmel_path.split('/')[-3], ''.join(uttr_spmel_path.split('/')[-2:]))
        alter_uttr_spmel_path = os.path.join(args.spmelFolder,alter_suffix)
        if os.path.isfile(alter_uttr_spmel_path):
            return get_uttr_melspect(alter_suffix)
        else:
            #TODO : implement auto-convert
            raise Exception(f'The spectogram for {uttr_wav_path} does not exist, auto-convert is not supported yet.')
    return mlspect

def converter(model_ckpt, source, target, spmelFolder, wavsFolder, metadata_dir,
 vocoder = 'checkpoint_step001000000_ema.pth', outputFolder ='results'):
    if not os.path.isdir(outputFolder):
        os.mkdir(outputFolder)
    source = source.replace('\\', '/')
    target = target.replace('\\', '/')
    source_person = source.split('/')[0]
    source_spmel_path =  os.path.join(source_person,''.join(source.split('/')[1:]))
    target_person = target.split('/')[0]
    with torch.no_grad():
        g_checkpoint = torch.load(args.model, map_location=device)
        default_hparams = {
            'dim_neck': 32,
            'dim_emb': 256,
            'dim_pre': 512,
            'freq': 32
        }
        hparams = g_checkpoint.get('hyperparams', default_hparams)
        G = Generator(hparams['dim_neck'],hparams['dim_emb'],hparams['dim_pre'],hparams['freq']).eval().to(device)

        G.load_state_dict(g_checkpoint.get('G_state_dict', g_checkpoint.get('model')))
        metadata = pickle.load(open(os.path.join(args.spmelFolder, args.metadata), "rb"))
        spect_vc = []

        emb_org = get_embedding(metadata, source_person)
        emb_trg = get_embedding(metadata, target_person)

        source_path = os.path.join(wavsFolder,source)
        if os.path.isfile(source_path):
            X_orgs = [source_spmel_path]
        elif os.path.isdir(source_path):
            X_orgs = [os.path.join(source,file) for _,_,files in os.walk(source_path) for file in files]
        else:
            raise Exception(f'Wrong path: {source_path}')

        for x_org_source in X_orgs:
            x_org_source = x_org_source.replace('\\', '/')
            source_file = '__'.join(x_org_source.split('/')[1:])
            x_org = get_uttr_melspect(x_org_source, spmelFolder=spmelFolder)
            x_org, len_pad = pad_seq(x_org)
            uttr_org = torch.from_numpy(x_org[np.newaxis, :, :]).to(device)

            _, x_identic_psnt, _ = G(uttr_org, emb_org, emb_trg)
            if len_pad == 0:
                uttr_trg = x_identic_psnt[0, 0, :, :].cpu().numpy()
            else:
                uttr_trg = x_identic_psnt[0, 0, :-len_pad, :].cpu().numpy()
            spect_vc.append( ('{}_{}_by_{}'.format(source_person,source_file[:-4], target_person), uttr_trg) )

        del G
        del g_checkpoint

        model = build_model().to(device)
        checkpoint = torch.load(vocoder, map_location=torch.device(device))
        model.load_state_dict(checkpoint["state_dict"])

        for spect in spect_vc:
            name = spect[0]
            c = spect[1]
            waveform = wavegen(model, c=c)
            sf.write(f'{outputFolder}/{name}.wav', waveform, samplerate=16000)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default='autovc.ckpt')
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--spmelFolder", default='./training_set/spmel')
    parser.add_argument("--wavsFolder", default='./training_set/wavs')
    parser.add_argument("--metadata", default='train.pkl')
    parser.add_argument("--vocoder", default='checkpoint_step001000000_ema.pth')
    parser.add_argument("--outputFolder", default='results')

    args = parser.parse_args()

    converter(model_ckpt= args.model, source=args.source, target=args.target,
     spmelFolder=args.spmelFolder, wavsFolder= args.wavsFolder,
      metadata_dir= args.metadata, vocoder=args.vocoder, outputFolder=args.outputFolder)
