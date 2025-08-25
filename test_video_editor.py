#!/usr/bin/env python3
"""
Script de teste para o editor de vídeo automático.
Testa o processamento do vídeo de teste criado.
"""

import os
import sys
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy import concatenate_videoclips
from pydub import AudioSegment
from pydub.silence import detect_silence

def test_video_processing():
    """Testa o processamento de vídeo sem interface gráfica."""
    
    input_path = "video_teste.mp4"
    output_path = "video_teste_editado.mp4"
    
    if not os.path.exists(input_path):
        print(f"Erro: Arquivo {input_path} não encontrado!")
        return False
    
    try:
        print("Iniciando teste do processamento de vídeo...")
        
        # Configurações
        silence_threshold = -40.0
        min_silence_duration = 1.0  # 1 segundo
        remove_speech_errors = True
        
        print("1. Carregando vídeo...")
        video = VideoFileClip(input_path)
        print(f"   Duração original: {video.duration:.2f}s")
        
        print("2. Extraindo áudio...")
        audio = video.audio
        temp_audio_path = "temp_test_audio.wav"
        audio.write_audiofile(temp_audio_path)
        
        print("3. Analisando áudio...")
        audio_segment = AudioSegment.from_wav(temp_audio_path)
        
        # Detectar silêncios
        min_silence_ms = int(min_silence_duration * 1000)
        silences = detect_silence(audio_segment, 
                                min_silence_len=min_silence_ms,
                                silence_thresh=silence_threshold)
        
        print(f"   Silêncios detectados: {len(silences)}")
        for i, (start, end) in enumerate(silences):
            print(f"   Silêncio {i+1}: {start/1000:.2f}s - {end/1000:.2f}s ({(end-start)/1000:.2f}s)")
        
        print("4. Detectando segmentos de fala...")
        speech_segments = []
        last_end = 0
        
        for start, end in silences:
            if start > last_end:
                speech_segments.append((last_end, start))
            last_end = end
            
        if last_end < len(audio_segment):
            speech_segments.append((last_end, len(audio_segment)))
        
        print(f"   Segmentos de fala detectados: {len(speech_segments)}")
        for i, (start, end) in enumerate(speech_segments):
            print(f"   Fala {i+1}: {start/1000:.2f}s - {end/1000:.2f}s ({(end-start)/1000:.2f}s)")
        
        # Remover erros de fala se solicitado
        if remove_speech_errors:
            print("5. Removendo erros de fala...")
            filtered_segments = []
            for start, end in speech_segments:
                duration = end - start
                if duration > 300:  # Manter apenas segmentos > 0.3s
                    filtered_segments.append((start, end))
                else:
                    print(f"   Removendo segmento curto: {start/1000:.2f}s - {end/1000:.2f}s ({duration/1000:.2f}s)")
            speech_segments = filtered_segments
            print(f"   Segmentos finais: {len(speech_segments)}")
        
        print("6. Criando vídeo editado...")
        final_clips = []
        total_duration = 0
        
        for i, (start, end) in enumerate(speech_segments):
            start_sec = start / 1000.0
            end_sec = end / 1000.0
            
            if end_sec - start_sec > 0:
                subclip = video.subclipped(start_sec, end_sec)
                final_clips.append(subclip)
                duration = end_sec - start_sec
                total_duration += duration
                print(f"   Subclipe {i+1}: {start_sec:.2f}s - {end_sec:.2f}s ({duration:.2f}s)")
        
        print("7. Salvando vídeo final...")
        if final_clips:
            final_video = concatenate_videoclips(final_clips)
            final_video.write_videofile(output_path, fps=24)
            final_video.close()
            print(f"   Duração final: {total_duration:.2f}s")
            print(f"   Redução: {((video.duration - total_duration) / video.duration * 100):.1f}%")
        else:
            print("   Nenhum segmento válido encontrado, copiando vídeo original...")
            video.write_videofile(output_path, fps=24)
        
        # Limpar recursos
        video.close()
        audio.close()
        
        # Remover arquivo temporário
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        
        print(f"\nTeste concluído com sucesso!")
        print(f"Vídeo editado salvo em: {output_path}")
        
        return True
        
    except Exception as e:
        print(f"Erro durante o teste: {str(e)}")
        return False

if __name__ == "__main__":
    success = test_video_processing()
    sys.exit(0 if success else 1)

