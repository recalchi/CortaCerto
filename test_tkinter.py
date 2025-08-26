import tkinter as tk

def main():
    root = tk.Tk()
    root.title("Teste Tkinter")
    root.geometry("300x200")

    label = tk.Label(root, text="Olá, Mundo!")
    label.pack(pady=50)

    root.mainloop()

if __name__ == "__main__":
    main()
