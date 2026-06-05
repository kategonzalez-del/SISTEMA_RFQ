import os
from google import genai

def ask_gemini(prompt):
    try:
        # 1. Recuperar la llave dinámica desde Render
        api_key_dinamica = os.environ.get('GEMINI_API_KEY')
        
        # 2. Inicializar el "Client" oficial de la nueva API
        client = genai.Client(api_key=api_key_dinamica)
        
        # 3. Llamar a tu modelo específico Flash Lite
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=prompt,
        )
        
        return response.text
        
    except Exception as e:
        print(f"Error crítico en la llamada a Gemini Flash Lite: {str(e)}")
        raise e