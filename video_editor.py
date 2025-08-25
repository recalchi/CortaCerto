import moviepy.editor as mp
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from pydub import AudioSegment
from pydub.silence import detect_silence

def remove_silence(video_path, output_path, silence_threshold=-40, min_silence_len=1000):
    # Carregar o vídeo
    video = mp.VideoFileClip(video_path)
    audio = video.audio

    # Salvar o áudio temporariamente
    audio_path = "temp_audio.wav"
    audio.write_audiofile(audio_path)

    # Carregar o áudio com pydub
    audio_segment = AudioSegment.from_wav(audio_path)

    # Detectar silêncios
    silences = detect_silence(audio_segment, min_silence_len=min_silence_len, silence_thresh=silence_threshold)

    # Inverter os silêncios para obter os segmentos de fala
    speech_segments = []
    last_end = 0
    for start, end in silences:
        if start > last_end:
            speech_segments.append((last_end, start))
        last_end = end
    if last_end < len(audio_segment):
        speech_segments.append((last_end, len(audio_segment)))

    # Criar subclipes e concatenar
    final_clips = []
    for start, end in speech_segments:
        start_sec = start / 1000.0
        end_sec = end / 1000.0
        if end_sec - start_sec > 0:
            final_clips.append(video.subclip(start_sec, end_sec))

    if final_clips:
        final_video = mp.concatenate_videoclips(final_clips)
        final_video.write_videofile(output_path, codec="libx264", audio_codec="aac")
    else:
        print("Nenhum segmento de fala detectado. O vídeo original será copiado.")
        video.write_videofile(output_path, codec="libx264", audio_codec="aac")

    # Limpar arquivos temporários
    import os
    os.remove(audio_path)

if __name__ == "__main__":
    # Exemplo de uso
    input_video = "input.mp4"  # Substitua pelo caminho do seu vídeo de entrada
    output_video = "output_no_silence.mp4" # Substitua pelo caminho do seu vídeo de saída
    # Crie um vídeo de teste ou use um existente
    # Para testar, você pode criar um vídeo curto com silêncios
    # Ex: from moviepy.editor import ColorClip, AudioFileClip, CompositeVideoClip
    # clip1 = ColorClip((640, 480), color=(255,0,0), duration=2)
    # silence = AudioSegment.silent(duration=1500) # 1.5 seconds of silence
    # audio_clip = AudioFileClip("some_audio.mp3") # Replace with a real audio file
    # final_audio = audio_clip + silence
    # final_video = clip1.set_audio(final_audio)
    # final_video.write_videofile(input_video)

    # Para este exemplo, vamos apenas criar um arquivo dummy para simular a existência
    # Em um cenário real, o usuário forneceria o caminho do vídeo.
    try:
        with open(input_video, 'w') as f:
            f.write('dummy video file')
        remove_silence(input_video, output_video)
        print(f"Processamento concluído. Vídeo salvo em: {output_video}")
    except Exception as e:
        print(f"Ocorreu um erro: {e}")
        print("Certifique-se de que o arquivo de vídeo de entrada existe e que as bibliotecas MoviePy, pydub e ffmpeg estão instaladas.")



