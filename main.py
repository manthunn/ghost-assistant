import os
from dotenv import load_dotenv
import ollama
import pyttsx3

load_dotenv()
engine = pyttsx3.init()
engine.setProperty("rate", 180)  # speaking speed

MODEL = "llama3.1"

history = [
    {"role": "system", "content": "You are Ghost, a sharp and helpful personal AI assistant. Keep replies short and conversational."}
]

def speak(text):
    engine.say(text)
    engine.runAndWait()

print("👻 Ghost is online (fully local). Type 'quit' to exit.\n")

while True:
    user_input = input("You: ")
    if user_input.lower() == "quit":
        print("Ghost: Vanishing... 👻")
        break

    history.append({"role": "user", "content": user_input})

    response = ollama.chat(model=MODEL, messages=history)
    reply = response["message"]["content"]

    history.append({"role": "assistant", "content": reply})
    print(f"\nGhost: {reply}\n")
    speak(reply)