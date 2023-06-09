import pypdf

import tiktoken

import openai

import os
import re
import pandas as pd

import streamlit as st

import numpy as np
from openai.embeddings_utils import distances_from_embeddings

def embededor(patrones_str, paginas_eliminar, carpeta):

  # Obtener la lista de archivos PDF en la carpeta especificada
  pdf_files = [f for f in os.listdir(carpeta) if f.endswith('.pdf')]

  # Crear una lista para almacenar los textos extraídos
  texts = []

  # Convertir los números de página ingresados por el usuario en una lista
  paginas_eliminar = [int(pagina) for pagina in paginas_eliminar.split(",")]

  patrones = patrones_str.split(",")

  # Calcular el número de patrones
  num_patrones = len(patrones)

  # Recorrer los archivos PDF y convertirlos a texto usando pypdf
  for file in pdf_files:

      with open(os.path.join(carpeta, file), 'rb') as pdf_file:
          # Abrir el archivo PDF en modo binario y crear un objeto PdfReader
          pdf = pypdf.PdfReader(pdf_file)

          # Crear una variable para almacenar el texto extraído de cada página del archivo PDF
          text = ''

          # Recorrer las páginas del archivo PDF y extraer el texto usando el método extract_text()
          for i, page in enumerate(pdf.pages):
              if i+1 in paginas_eliminar:
                  continue
              text += page.extract_text()

          # Eliminar los patrones del texto usando expresiones regulares
          for patron in patrones:
              pattern = re.compile(patron)
              text = re.sub(pattern, '', text)
          
          # Añadir el nombre del archivo y el texto extraído a la lista texts
          texts.append((file[:-4].replace('-',' ').replace('_', ' ').replace('#update',''), text))

  # Crear un dataframe con las columnas fname (nombre del archivo) y text (texto extraído)
  df = pd.DataFrame(texts, columns=['fname', 'text'])

  # Guardar el dataframe como un archivo CSV en la misma carpeta que los archivos PDF originales
  df.to_csv(os.path.join(carpeta, 'texts.csv'))

  # Load the cl100k_base tokenizer which is designed to work with the ada-002 model
  tokenizer = tiktoken.get_encoding("cl100k_base")

  df = pd.read_csv(os.path.join(carpeta, 'texts.csv'))
  df.columns = ['title', 'text']

  # Tokenize the text and save the number of tokens to a new column
  df['n_tokens'] = df.text.apply(lambda x: len(tokenizer.encode(x)))


  max_tokens = 500

  # Function to split the text into chunks of a maximum number of tokens
  def split_into_many(text, max_tokens = max_tokens):

      # Split the text into sentences
      sentences = text.split('. ')

      # Get the number of tokens for each sentence
      n_tokens = [len(tokenizer.encode(" " + sentence)) for sentence in sentences]
      
      chunks = []
      tokens_so_far = 0
      chunk = []

      # Loop through the sentences and tokens joined together in a tuple
      for sentence, token in zip(sentences, n_tokens):

          # If the number of tokens so far plus the number of tokens in the current sentence is greater 
          # than the max number of tokens, then add the chunk to the list of chunks and reset
          # the chunk and tokens so far
          if tokens_so_far + token > max_tokens:
              chunks.append(". ".join(chunk) + ".")
              chunk = []
              tokens_so_far = 0

          # If the number of tokens in the current sentence is greater than the max number of 
          # tokens, go to the next sentence
          if token > max_tokens:
              continue

          # Otherwise, add the sentence to the chunk and add the number of tokens to the total
          chunk.append(sentence)
          tokens_so_far += token + 1

      return chunks

  shortened = []

  # Loop through the dataframe
  for row in df.iterrows():

      # If the text is None, go to the next row
      if row[1]['text'] is None:
          continue

      # If the number of tokens is greater than the max number of tokens, split the text into chunks
      if row[1]['n_tokens'] > max_tokens:
          shortened += split_into_many(row[1]['text'])
      
      # Otherwise, add the text to the list of shortened texts
      else:
          shortened.append( row[1]['text'] )

  df = pd.DataFrame(shortened, columns = ['text'])
  df['n_tokens'] = df.text.apply(lambda x: len(tokenizer.encode(x)))


  df['embeddings'] = df.text.apply(lambda x: openai.Embedding.create(input=x, engine='text-embedding-ada-002')['data'][0]['embedding'])

  df.to_csv(os.path.join(carpeta, 'embeddings.csv'))

  return df

def create_context(
    question, df, max_len=1800, size="ada"
):
    """
    Create a context for a question by finding the most similar context from the dataframe
    """

    # Get the embeddings for the question
    q_embeddings = openai.Embedding.create(input=question, engine='text-embedding-ada-002')['data'][0]['embedding']

    # Get the distances from the embeddings
    df['distances'] = distances_from_embeddings(q_embeddings, df['embeddings'].values, distance_metric='cosine')


    returns = []
    cur_len = 0

    # Sort by distance and add the text to the context until the context is too long
    for i, row in df.sort_values('distances', ascending=True).iterrows():
        
        # Add the length of the text to the current length
        cur_len += row['n_tokens'] + 4
        
        # If the context is too long, break
        if cur_len > max_len:
            break
        
        # Else add it to the text that is being returned
        returns.append(row["text"])

    # Return the context
    return "\n\n###\n\n".join(returns)

def answer_question(
    df,
    model="gpt-3.5-turbo",
    question="pregunta",
    max_len=1800,
    size="ada",
    debug=False,
    max_tokens=150,
    stop_sequence=None
):
    """
    Answer a question based on the most similar context from the dataframe texts
    """
    context = create_context(
        question,
        df,
        max_len=max_len,
        size=size,
    )
    # If debug, print the raw model response
    if debug:
        print("Contexto:\n" + context)
        print("\n\n")
    
    try:
        system="Eres un experto en responder preguntas basándose en el contexto y no alucinando con la información. Sea honesto y preciso"
        prompt=f"Responde a la pregunta basándote en el contexto que aparece a continuación, selecciona una opción y escribe sólo la opción sin más texto, y si la pregunta no se puede responder basándote en el contexto, di \"No lo sé\"\n\nContexto: {context}\n\n---\n\nPregunta: {question}\nRespuesta:"
        conversation = [
            {"role": "system", "content": system}, {"role": "user", "content": prompt}
        ]
        
        # Create a completions using the question and context
        response = openai.ChatCompletion.create(
            messages = conversation,
            temperature=0,
            max_tokens=max_tokens,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            stop=stop_sequence,
            model=model,
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        print(e)
        return ""

st.title("📒 Prueba 🤖 ")

user_secret = st.text_input(label = ":blue[OpenAI API key]",
                                placeholder = "Copia aquí tu clave de OpenAI",
                                type = "password")
if user_secret:
    openai.api_key = user_secret

    opciones1 = ["PDFs","Webs"]
    opciones2 = ["Subir PDFs","Subir los PDFs indexados"]

    eleccion1 = st.selectbox("Selecciona una opción", opciones1)

    if eleccion1 == "PDFs":
      eleccion2 = st.selectbox("Selecciona una opción", opciones2)
      if eleccion2 == "Subir PDFs":
        # Creamos la carpeta "data" si no existe previamente
        if not os.path.exists("data"):
            os.makedirs("data")

        uploaded_files = st.file_uploader("Selecciona los archivos PDF", type="pdf", accept_multiple_files=True)

        if uploaded_files is not None:
            for uploaded_file in uploaded_files:
                file_path = os.path.join("data", uploaded_file.name)
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                st.write("Archivo guardado:", uploaded_file.name)
                
        st.write("¿Quieres eliminar algún patrón de texto en tus archivos, que ensucie el texto de tus archivos? Por ejemplo: marcas de agua o encabezados")
        eleccion3 = st.checkbox("Eliminar patrones")
        st.write("¿Quieres eliminar algunas de las páginas de tus archivos? Por ejemplo: la portada o el índice")
        eleccion4 = st.checkbox("¿Quieres eliminar algunas de las páginas de tus archivos?")
        if eleccion3 == True:
            patrones_str = st.text_input("Ingrese los patrones separados por comas: ")
        if eleccion4 == True:
            paginas_eliminar = st.text_input("Escribe los números de página separados por comas a eliminar (por ejemplo, 1,2,5): ")

        carpeta = os.path.abspath("data")    
        archivo_df = embededor(patrones_str, paginas_eliminar, carpeta)
        st.write("Guarda este archivo .csv que contiene el texto de los archivos pdfs indexados. Úsalo cada vez que abras la aplicación para buscar nuevamente")
        if st.button('Descargar archivo'):
          csv = archivo_df.to_csv(index=False)
          b64 = base64.b64encode(csv.encode()).decode()  # Codificar a base64
          href = f'data:text/csv;base64,{b64}'  # Crear el enlace para descargar el archivo
          st.markdown(f'<a href="{href}" download="data.csv">Descargar archivo</a>', unsafe_allow_html=True)
        
        archivo_df['embeddings'] = archivo_df['embeddings'].apply(eval).apply(np.array)


        pregunta = st.text_input(
            label=":blue[Pregunta lo que quieras]",
            placeholder="Por favor, responde a la pregunta..."
        )
        
        if pregunta:
            # And if they have clicked the search button
            if st.button(label="Buscar", type='primary'):
                # Run the search function and get the results
                st.write(answer_question(archivo_df, pregunta))

      else:
        csv_file = st.file_uploader("Selecciona el archivo .csv con el texto indexado", type="csv")

        if csv_file is not None:
          archivo_df = pd.read_csv(csv_file)
        
        archivo_df['embeddings'] = archivo_df['embeddings'].apply(eval).apply(np.array)

        pregunta = st.text_input(
            label=":blue[Pregunta lo que quieras]",
            placeholder="Por favor, responde a la pregunta..."
        )
        
        if pregunta:
            # And if they have clicked the search button
            if st.button(label="Buscar", type='primary'):
                # Run the search function and get the results
                st.write(answer_question(archivo_df, pregunta))
