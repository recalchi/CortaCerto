import sys

print("=== TESTE DE LIBS PYTHON ===\n")

# Teste Tkinter
try:
    import tkinter as tk
    root = tk.Tk()
    root.title("Teste Tkinter OK ✅")
    root.geometry("250x100")
    label = tk.Label(root, text="Tkinter está funcionando!", font=("Arial", 10))
    label.pack(pady=20)
    root.after(2000, root.destroy)  # Fecha sozinho após 2 segundos
    root.mainloop()
    print("Tkinter OK ✅")
except Exception as e:
    print("Erro no Tkinter ❌ ->", e)

# Teste MoviePy
try:
    import moviepy
    print("MoviePy OK ✅ (versão:", moviepy.__version__, ")")
except Exception as e:
    print("Erro no MoviePy ❌ ->", e)

# Teste Pydub
try:
    import pydub
    print("Pydub OK ✅ (versão:", pydub.__version__, ")")
except Exception as e:
    print("Erro no Pydub ❌ ->", e)

# Teste SpeechRecognition
try:
    import speech_recognition as sr
    print("SpeechRecognition OK ✅ (versão:", sr.__version__, ")")
except Exception as e:
    print("Erro no SpeechRecognition ❌ ->", e)

print("\n=== FIM DO TESTE ===")
