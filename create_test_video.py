from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.VideoClip import ColorClip, TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
import numpy as np
from pydub import AudioSegment
from pydub.generators import Sine
import os

def create_test_video():
    """Cria um vídeo de teste com fala e silêncios para testar o editor."""
    
    # Criar um vídeo simples com texto
    duration = 10  # 10 segundos
    
    # Criar clipe de vídeo com cor sólida e texto
    video_clip = ColorClip(size=(640, 480), color=(50, 50, 150), duration=duration)
    
    # Adicionar texto
    txt_clip = TextClip("Vídeo de Teste", fontsize=50, color='white', font='Arial')
    txt_clip = txt_clip.set_position('center').set_duration(duration)
    
    # Compor vídeo com texto
    video = CompositeVideoClip([video_clip, txt_clip])
    
    # Criar áudio com fala simulada e silêncios
    # Vamos criar um áudio com:
    # 0-2s: "fala" (tom)
    # 2-4s: silêncio
    # 4-6s: "fala" (tom)
    # 6-8s: silêncio longo (deve ser removido)
    # 8-10s: "fala" (tom)
    
    # Criar tons para simular fala
    tone_freq = 440  # Frequência do tom (Hz)
    sample_rate = 44100
    
    # Segmento 1: "fala" (0-2s)
    fala1 = Sine(tone_freq).to_audio_segment(duration=2000)
    
    # Silêncio curto (2-2.5s) - deve ser mantido
    silencio_curto = AudioSegment.silent(duration=500)
    
    # Segmento 2: "fala" (2.5-4.5s)
    fala2 = Sine(tone_freq + 100).to_audio_segment(duration=2000)
    
    # Silêncio longo (4.5-7s) - deve ser removido
    silencio_longo = AudioSegment.silent(duration=2500)
    
    # Segmento 3: "fala" (7-10s)
    fala3 = Sine(tone_freq + 200).to_audio_segment(duration=3000)
    
    # Combinar todos os segmentos
    audio_completo = fala1 + silencio_curto + fala2 + silencio_longo + fala3
    
    # Salvar áudio temporário
    temp_audio_path = "temp_test_audio.wav"
    audio_completo.export(temp_audio_path, format="wav")
    
    # Carregar áudio no MoviePy
    audio_clip = AudioFileClip(temp_audio_path)
    
    # Definir áudio no vídeo
    final_video = video.set_audio(audio_clip)
    
    # Salvar vídeo de teste
    output_path = "video_teste.mp4"
    final_video.write_videofile(output_path, codec="libx264", audio_codec="aac", verbose=False, logger=None)
    
    # Limpar arquivos temporários
    os.remove(temp_audio_path)
    
    # Fechar clipes
    final_video.close()
    audio_clip.close()
    video.close()
    
    print(f"Vídeo de teste criado: {output_path}")
    print("Estrutura do vídeo:")
    print("0-2s: Fala (tom 440Hz)")
    print("2-2.5s: Silêncio curto (deve ser mantido)")
    print("2.5-4.5s: Fala (tom 540Hz)")
    print("4.5-7s: Silêncio longo (deve ser removido)")
    print("7-10s: Fala (tom 640Hz)")
    
    return output_path

if __name__ == "__main__":
    create_test_video()

