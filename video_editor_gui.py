import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import os
import moviepy.editor as mp
from pydub import AudioSegment
from pydub.silence import detect_silence
import speech_recognition as sr
import re

class VideoEditorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Editor de Vídeo Automático")
        self.root.geometry("600x500")
        self.root.configure(bg="#f0f0f0")
        
        # Variáveis
        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        self.silence_threshold = tk.DoubleVar(value=-40.0)
        self.min_silence_duration = tk.DoubleVar(value=1.0)
        self.remove_speech_errors = tk.BooleanVar(value=True)
        self.processing = False
        
        self.create_widgets()
        
    def create_widgets(self):
        # Título
        title_label = tk.Label(self.root, text="Editor de Vídeo Automático", 
                              font=("Arial", 16, "bold"), bg="#f0f0f0")
        title_label.pack(pady=10)
        
        # Frame principal
        main_frame = tk.Frame(self.root, bg="#f0f0f0")
        main_frame.pack(padx=20, pady=10, fill="both", expand=True)
        
        # Seleção de arquivo de entrada
        input_frame = tk.LabelFrame(main_frame, text="Arquivo de Vídeo", 
                                   font=("Arial", 10, "bold"), bg="#f0f0f0")
        input_frame.pack(fill="x", pady=5)
        
        tk.Entry(input_frame, textvariable=self.input_file, width=50).pack(side="left", padx=5, pady=5)
        tk.Button(input_frame, text="Procurar", command=self.browse_input_file).pack(side="right", padx=5, pady=5)
        
        # Pasta de saída
        output_frame = tk.LabelFrame(main_frame, text="Pasta de Saída", 
                                    font=("Arial", 10, "bold"), bg="#f0f0f0")
        output_frame.pack(fill="x", pady=5)
        
        tk.Entry(output_frame, textvariable=self.output_folder, width=50).pack(side="left", padx=5, pady=5)
        tk.Button(output_frame, text="Procurar", command=self.browse_output_folder).pack(side="right", padx=5, pady=5)
        
        # Configurações
        config_frame = tk.LabelFrame(main_frame, text="Configurações", 
                                    font=("Arial", 10, "bold"), bg="#f0f0f0")
        config_frame.pack(fill="x", pady=5)
        
        # Threshold de silêncio
        silence_frame = tk.Frame(config_frame, bg="#f0f0f0")
        silence_frame.pack(fill="x", padx=5, pady=2)
        tk.Label(silence_frame, text="Threshold de Silêncio (dB):", bg="#f0f0f0").pack(side="left")
        tk.Scale(silence_frame, from_=-60, to=-10, resolution=1, orient="horizontal", 
                variable=self.silence_threshold).pack(side="right", fill="x", expand=True)
        
        # Duração mínima de silêncio
        duration_frame = tk.Frame(config_frame, bg="#f0f0f0")
        duration_frame.pack(fill="x", padx=5, pady=2)
        tk.Label(duration_frame, text="Duração Mínima de Silêncio (s):", bg="#f0f0f0").pack(side="left")
        tk.Scale(duration_frame, from_=0.5, to=5.0, resolution=0.1, orient="horizontal", 
                variable=self.min_silence_duration).pack(side="right", fill="x", expand=True)
        
        # Opção para remover erros de fala
        tk.Checkbutton(config_frame, text="Remover erros de fala (eeeeh, aah, etc.)", 
                      variable=self.remove_speech_errors, bg="#f0f0f0").pack(anchor="w", padx=5, pady=5)
        
        # Botão de processamento
        self.process_button = tk.Button(main_frame, text="Processar Vídeo", 
                                       command=self.start_processing, 
                                       font=("Arial", 12, "bold"),
                                       bg="#4CAF50", fg="white", height=2)
        self.process_button.pack(pady=20)
        
        # Barra de progresso
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, 
                                           maximum=100, length=400)
        self.progress_bar.pack(pady=10)
        
        # Label de status
        self.status_label = tk.Label(main_frame, text="Pronto para processar", 
                                    bg="#f0f0f0", font=("Arial", 10))
        self.status_label.pack(pady=5)
        
    def browse_input_file(self):
        filename = filedialog.askopenfilename(
            title="Selecionar arquivo de vídeo",
            filetypes=[("Arquivos de vídeo", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv")]
        )
        if filename:
            self.input_file.set(filename)
            
    def browse_output_folder(self):
        folder = filedialog.askdirectory(title="Selecionar pasta de saída")
        if folder:
            self.output_folder.set(folder)
            
    def update_status(self, message):
        self.status_label.config(text=message)
        self.root.update_idletasks()
        
    def update_progress(self, value):
        self.progress_var.set(value)
        self.root.update_idletasks()
        
    def start_processing(self):
        if not self.input_file.get():
            messagebox.showerror("Erro", "Por favor, selecione um arquivo de vídeo.")
            return
            
        if not os.path.exists(self.input_file.get()):
            messagebox.showerror("Erro", "O arquivo de vídeo selecionado não existe.")
            return
            
        if self.processing:
            messagebox.showwarning("Aviso", "Já existe um processamento em andamento.")
            return
            
        # Iniciar processamento em thread separada
        self.processing = True
        self.process_button.config(state="disabled", text="Processando...")
        
        thread = threading.Thread(target=self.process_video)
        thread.daemon = True
        thread.start()
        
    def detect_speech_errors(self, audio_segment):
        """Detecta erros de fala como 'eeeeh', 'aah', etc."""
        # Esta é uma implementação simplificada
        # Em um cenário real, seria necessário usar reconhecimento de fala mais avançado
        
        # Padrões comuns de erros de fala
        error_patterns = [
            r'\b(uh+|um+|er+|ah+|eh+)\b',  # uh, um, er, ah, eh
            r'\b(eee+h*|aaa+h*|ooo+h*)\b',  # eeeeh, aaah, oooh
            r'\b(hmm+|huh+|mhm+)\b'  # hmm, huh, mhm
        ]
        
        # Para esta implementação simplificada, vamos apenas detectar
        # segmentos muito curtos (< 0.5s) que podem ser erros de fala
        segments_to_remove = []
        
        # Detectar segmentos muito curtos que podem ser erros
        silence_segments = detect_silence(audio_segment, 
                                        min_silence_len=100,  # 0.1s
                                        silence_thresh=self.silence_threshold.get())
        
        speech_segments = []
        last_end = 0
        for start, end in silence_segments:
            if start > last_end:
                duration = start - last_end
                if duration < 500:  # Menos de 0.5s pode ser erro de fala
                    segments_to_remove.append((last_end, start))
                else:
                    speech_segments.append((last_end, start))
            last_end = end
            
        if last_end < len(audio_segment):
            duration = len(audio_segment) - last_end
            if duration < 500:
                segments_to_remove.append((last_end, len(audio_segment)))
            else:
                speech_segments.append((last_end, len(audio_segment)))
                
        return segments_to_remove
        
    def process_video(self):
        try:
            input_path = self.input_file.get()
            output_folder = self.output_folder.get()
            
            # Criar nome do arquivo de saída
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(output_folder, f"{base_name}_editado.mp4")
            
            self.update_status("Carregando vídeo...")
            self.update_progress(10)
            
            # Carregar o vídeo
            video = mp.VideoFileClip(input_path)
            
            self.update_status("Extraindo áudio...")
            self.update_progress(20)
            
            # Extrair áudio
            audio = video.audio
            temp_audio_path = "temp_audio.wav"
            audio.write_audiofile(temp_audio_path, verbose=False, logger=None)
            
            self.update_status("Analisando áudio...")
            self.update_progress(30)
            
            # Carregar áudio com pydub
            audio_segment = AudioSegment.from_wav(temp_audio_path)
            
            # Detectar silêncios
            min_silence_ms = int(self.min_silence_duration.get() * 1000)
            silences = detect_silence(audio_segment, 
                                    min_silence_len=min_silence_ms,
                                    silence_thresh=self.silence_threshold.get())
            
            self.update_status("Detectando segmentos de fala...")
            self.update_progress(50)
            
            # Obter segmentos de fala (inverso dos silêncios)
            speech_segments = []
            last_end = 0
            
            for start, end in silences:
                if start > last_end:
                    speech_segments.append((last_end, start))
                last_end = end
                
            if last_end < len(audio_segment):
                speech_segments.append((last_end, len(audio_segment)))
            
            # Remover erros de fala se solicitado
            if self.remove_speech_errors.get():
                self.update_status("Detectando erros de fala...")
                self.update_progress(60)
                
                # Filtrar segmentos muito curtos (possíveis erros de fala)
                filtered_segments = []
                for start, end in speech_segments:
                    duration = end - start
                    if duration > 300:  # Manter apenas segmentos > 0.3s
                        filtered_segments.append((start, end))
                speech_segments = filtered_segments
            
            self.update_status("Criando vídeo editado...")
            self.update_progress(70)
            
            # Criar subclipes e concatenar
            final_clips = []
            for i, (start, end) in enumerate(speech_segments):
                start_sec = start / 1000.0
                end_sec = end / 1000.0
                
                if end_sec - start_sec > 0:
                    subclip = video.subclip(start_sec, end_sec)
                    final_clips.append(subclip)
                    
                # Atualizar progresso
                progress = 70 + (i / len(speech_segments)) * 20
                self.update_progress(progress)
            
            self.update_status("Salvando vídeo final...")
            self.update_progress(90)
            
            if final_clips:
                final_video = mp.concatenate_videoclips(final_clips)
                final_video.write_videofile(output_path, 
                                          codec="libx264", 
                                          audio_codec="aac",
                                          verbose=False, 
                                          logger=None)
                final_video.close()
            else:
                # Se não há segmentos, copiar o vídeo original
                video.write_videofile(output_path, 
                                    codec="libx264", 
                                    audio_codec="aac",
                                    verbose=False, 
                                    logger=None)
            
            # Limpar recursos
            video.close()
            audio.close()
            
            # Remover arquivo temporário
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
            
            self.update_progress(100)
            self.update_status("Processamento concluído!")
            
            messagebox.showinfo("Sucesso", 
                              f"Vídeo processado com sucesso!\nSalvo em: {output_path}")
            
        except Exception as e:
            self.update_status("Erro durante o processamento")
            messagebox.showerror("Erro", f"Ocorreu um erro durante o processamento:\n{str(e)}")
            
        finally:
            self.processing = False
            self.process_button.config(state="normal", text="Processar Vídeo")
            self.update_progress(0)

def main():
    root = tk.Tk()
    app = VideoEditorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()

