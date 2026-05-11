import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ttkthemes import ThemedTk
import threading
import os
from moviepy.editor import VideoFileClip, concatenate_videoclips
from pydub import AudioSegment
from pydub.silence import detect_silence
import time

class VideoEditorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CortaCerto")
        self.root.geometry("700x600")
        self.root.configure(bg="#2b2b2b")

        # Variáveis
        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        self.silence_threshold = tk.DoubleVar(value=-40.0)
        self.min_silence_duration = tk.DoubleVar(value=1.0)
        self.remove_speech_errors = tk.BooleanVar(value=True)
        self.use_gpu_acceleration = tk.BooleanVar(value=False)
        self.audio_padding_ms = 150
        self.processing = False
        self.cancel_flag = False

        self.create_widgets()
        self.apply_dark_theme()

    # ================== TEMA ESCURO ==================
    def apply_dark_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TLabel", background="#2b2b2b", foreground="white")
        style.configure("TButton", background="#3a3a3a", foreground="white")
        style.configure("TCheckbutton", background="#2b2b2b", foreground="white")
        style.configure("TLabelFrame", background="#2b2b2b", foreground="white")
        style.configure("TProgressbar", troughcolor="#3a3a3a", background="#4CAF50")

    # ================== WIDGETS ==================
    def create_widgets(self):
        title_label = tk.Label(self.root, text="Editor de Vídeo Automático", font=("Arial", 16, "bold"),
                               bg="#2b2b2b", fg="white")
        title_label.pack(pady=10)

        main_frame = tk.Frame(self.root, bg="#2b2b2b")
        main_frame.pack(padx=15, pady=10, fill="both", expand=True)

        # Input file
        input_frame = tk.LabelFrame(main_frame, text="Arquivo de Vídeo", bg="#2b2b2b", fg="white")
        input_frame.pack(fill="x", pady=5)
        tk.Entry(input_frame, textvariable=self.input_file, width=50, bg="#3a3a3a", fg="white").pack(side="left", padx=5, pady=5)
        tk.Button(input_frame, text="Procurar", command=self.browse_input_file, bg="#4a4a4a", fg="white").pack(side="right", padx=5, pady=5)

        # Output folder
        output_frame = tk.LabelFrame(main_frame, text="Pasta de Saída", bg="#2b2b2b", fg="white")
        output_frame.pack(fill="x", pady=5)
        tk.Entry(output_frame, textvariable=self.output_folder, width=50, bg="#3a3a3a", fg="white").pack(side="left", padx=5, pady=5)
        tk.Button(output_frame, text="Procurar", command=self.browse_output_folder, bg="#4a4a4a", fg="white").pack(side="right", padx=5, pady=5)

        # Configurações
        config_frame = tk.LabelFrame(main_frame, text="Configurações", bg="#2b2b2b", fg="white")
        config_frame.pack(fill="x", pady=5)
        tk.Label(config_frame, text="Threshold de Silêncio (dB):", bg="#2b2b2b", fg="white").pack(anchor="w", padx=5)
        tk.Scale(config_frame, from_=-60, to=-10, resolution=1, orient="horizontal", variable=self.silence_threshold,
                 bg="#2b2b2b", fg="white", troughcolor="#4a4a4a").pack(fill="x", padx=5)

        tk.Label(config_frame, text="Duração mínima de silêncio (s):", bg="#2b2b2b", fg="white").pack(anchor="w", padx=5)
        tk.Scale(config_frame, from_=0.5, to=5.0, resolution=0.1, orient="horizontal", variable=self.min_silence_duration,
                 bg="#2b2b2b", fg="white", troughcolor="#4a4a4a").pack(fill="x", padx=5)

        ttk.Checkbutton(config_frame, text="Remover erros de fala", variable=self.remove_speech_errors).pack(anchor="w", padx=5)
        ttk.Checkbutton(config_frame, text="Usar aceleração GPU (NVENC)", variable=self.use_gpu_acceleration).pack(anchor="w", padx=5)

        # Botões
        buttons_frame = tk.Frame(main_frame, bg="#2b2b2b")
        buttons_frame.pack(pady=10)
        self.process_button = tk.Button(buttons_frame, text="Processar Vídeo", bg="#4CAF50", fg="white", font=("Arial", 12, "bold"), command=self.start_processing)
        self.process_button.pack(side="left", padx=10)
        self.cancel_button = tk.Button(buttons_frame, text="Cancelar", bg="#f44336", fg="white", font=("Arial", 12, "bold"), command=self.cancel_processing, state="disabled")
        self.cancel_button.pack(side="right", padx=10)

        # Barra de progresso
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100, length=650)
        self.progress_bar.pack(pady=10)

        # Log
        self.log_text = tk.Text(main_frame, height=10, bg="#1e1e1e", fg="white", state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=5)

        # Status e ETA
        status_frame = tk.Frame(main_frame, bg="#2b2b2b")
        status_frame.pack(fill="x")
        self.status_label = tk.Label(status_frame, text="Pronto para processar", bg="#2b2b2b", fg="white", font=("Arial", 10))
        self.status_label.pack(side="left")
        self.eta_label = tk.Label(status_frame, text="ETA: --:--:--", bg="#2b2b2b", fg="white", font=("Arial", 10))
        self.eta_label.pack(side="right")

    # ================== MÉTODOS DE NAVEGAÇÃO ==================
    def browse_input_file(self):
        filename = filedialog.askopenfilename(title="Selecionar arquivo de vídeo",
                                              filetypes=[("Arquivos de vídeo", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv")])
        if filename:
            self.input_file.set(filename)

    def browse_output_folder(self):
        folder = filedialog.askdirectory(title="Selecionar pasta de saída")
        if folder:
            self.output_folder.set(folder)

    # ================== MÉTODOS DE LOG, STATUS, PROGRESS ==================
    def update_status(self, message):
        self.status_label.config(text=message)
        self.root.update_idletasks()

    def update_progress(self, value):
        self.progress_var.set(value)
        self.root.update_idletasks()

    def log_message(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def update_eta(self, eta_seconds):
        if eta_seconds is None:
            self.eta_label.config(text="ETA: --:--:--")
        else:
            hours, remainder = divmod(int(eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            self.eta_label.config(text=f"ETA: {hours:02}:{minutes:02}:{seconds:02}")
        self.root.update_idletasks()

    # ================== PROCESSAMENTO ==================
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

        self.processing = True
        self.cancel_flag = False
        self.process_button.config(state="disabled", text="Processando...")
        self.cancel_button.config(state="normal")
        thread = threading.Thread(target=self.process_video)
        thread.daemon = True
        thread.start()

    def cancel_processing(self):
        if messagebox.askyesno("Cancelar Processamento", "Tem certeza que deseja cancelar o processamento?"):
            self.cancel_flag = True
            self.update_status("Cancelando...")
            self.log_message("Solicitação de cancelamento recebida.")

    def process_video(self):
        try:
            input_path = self.input_file.get()
            output_folder = self.output_folder.get()
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(output_folder, f"{base_name}_editado.mp4")

            self.log_message("Carregando vídeo...")
            self.update_status("Carregando vídeo...")
            self.update_progress(10)
            if self.cancel_flag: raise InterruptedError()

            video = VideoFileClip(input_path)
            start_time = time.time()

            self.log_message("Extraindo áudio...")
            self.update_status("Extraindo áudio...")
            self.update_progress(20)
            if self.cancel_flag: raise InterruptedError()

            audio = video.audio
            temp_audio_path = "temp_audio.wav"
            audio.write_audiofile(temp_audio_path)

            audio_segment = AudioSegment.from_wav(temp_audio_path)
            min_silence_ms = int(self.min_silence_duration.get() * 1000)
            silences = detect_silence(audio_segment, min_silence_len=min_silence_ms,
                                      silence_thresh=self.silence_threshold.get())

            self.log_message("Detectando segmentos de fala...")
            self.update_status("Detectando segmentos de fala...")
            self.update_progress(50)
            if self.cancel_flag: raise InterruptedError()

            # Processamento de segmentos e padding
            speech_segments = []
            last_end = 0
            for start, end in silences:
                if start > last_end:
                    padded_start = max(0, last_end - self.audio_padding_ms)
                    padded_end = min(len(audio_segment), start + self.audio_padding_ms)
                    speech_segments.append((padded_start, padded_end))
                last_end = end
            if last_end < len(audio_segment):
                padded_start = max(0, last_end - self.audio_padding_ms)
                padded_end = min(len(audio_segment), len(audio_segment) + self.audio_padding_ms)
                speech_segments.append((padded_start, padded_end))

            # Consolidar segmentos
            # Consolidar segmentos sobrepostos
            if speech_segments:
                consolidated = [speech_segments[0]]
                for start, end in speech_segments[1:]:
                    prev_start, prev_end = consolidated[-1]
                    if start <= prev_end:
                        consolidated[-1] = (prev_start, max(prev_end, end))
                    else:
                        consolidated.append((start, end))
                speech_segments = consolidated

            # Remover erros de fala curtos
            if self.remove_speech_errors.get():
                filtered = []
                for start, end in speech_segments:
                    if end - start > 300:  # >300ms
                        filtered.append((start, end))
                speech_segments = filtered

            # Criar subclips
            final_clips = []
            for i, (start, end) in enumerate(speech_segments):
                if self.cancel_flag: raise InterruptedError()
                start_sec = start / 1000.0
                end_sec = end / 1000.0
                if end_sec - start_sec > 0:
                    subclip = video.subclip(start_sec, end_sec)
                    final_clips.append(subclip)
                # Atualizar progresso
                progress = 70 + (i / max(1, len(speech_segments))) * 20
                self.update_progress(progress)
                elapsed_time = time.time() - start_time
                if progress > 0:
                    eta = (elapsed_time / progress) * (100 - progress)
                    self.update_eta(eta)

            # ================== EXPORTAR VÍDEO FINAL ==================
            self.log_message("Salvando vídeo final...")
            self.update_status("Salvando vídeo final...")
            self.update_progress(90)
            if self.cancel_flag: raise InterruptedError()

            if final_clips:
                final_video = concatenate_videoclips(final_clips, method="compose")

                # Mantendo quase 100% da qualidade original
                final_video.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec="aac",
                    temp_audiofile="temp-audio.m4a",
                    remove_temp=True,
                    preset="ultrafast",  # Rápido e quase sem perda
                    ffmpeg_params=["-crf", "18"],  # Qualidade quase idêntica ao original
                    threads=os.cpu_count()
                )
                final_video.close()
            else:
                # Nenhum corte, copiar vídeo original
                video.write_videofile(
                    output_path,
                    codec="copy",
                    audio_codec="copy",
                    threads=os.cpu_count()
                )

            video.close()
            audio.close()
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)

            self.update_progress(100)
            self.update_eta(None)
            self.log_message("Processamento concluído!")
            self.update_status("Processamento concluído!")
            messagebox.showinfo("Sucesso", f"Vídeo processado com sucesso!\nSalvo em: {output_path}")

        except InterruptedError:
            self.log_message("Processamento cancelado.")
            self.update_status("Processamento cancelado.")
            messagebox.showinfo("Cancelado", "O processamento foi cancelado pelo usuário.")
        except Exception as e:
            self.log_message(f"Erro durante o processamento: {e}")
            self.update_status("Erro durante o processamento")
            messagebox.showerror("Erro", f"Ocorreu um erro durante o processamento:\n{str(e)}")
        finally:
            self.processing = False
            self.process_button.config(state="normal", text="Processar Vídeo")
            self.cancel_button.config(state="disabled")
            self.update_progress(0)
            self.update_eta(None)


# ================== MAIN ==================
def main():
    root = ThemedTk(theme="black")
    app = VideoEditorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
