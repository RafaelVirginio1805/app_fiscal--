from flask import Flask, render_template, request, send_file, jsonify
import os
import pandas as pd
import pymysql
from pymysql.err import MySQLError
from pymysql.cursors import DictCursor
from datetime import datetime
from PIL import Image
import utm
import io
import unicodedata
import re
import openpyxl

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

CSV_PATH = 'data/cidades.csv'
CSV_PATH1 = 'data/tipo.csv'
os.makedirs('data', exist_ok=True)

if not os.path.exists(CSV_PATH):
    dados_iniciais = {
        'cidade': ['Salvador', 'Salvador', 'Feira de Santana'],
        'bairro': ['Brotas', 'Pituba', 'Centro']
    }
    pd.DataFrame(dados_iniciais).to_csv(CSV_PATH, index=False)

def carregar_cidades():
    df = pd.read_csv(CSV_PATH)
    df = df.dropna(subset=['CIDADE', 'BAIRRO'])
    cidades_dict = {}
    for _, row in df.iterrows():
        cidade = str(row['CIDADE']).strip().title()
        bairro = str(row['BAIRRO']).strip().title()
        cidades_dict.setdefault(cidade, []).append(bairro)
    return cidades_dict

def carregar_tipo():
    if not os.path.exists(CSV_PATH1):
        return []
    df = pd.read_csv(CSV_PATH1)
    return df['TIPO'].dropna().str.strip().str.title().unique().tolist()

CSV_EQUIPAMENTOS = 'data/equipamento.csv'
CSV_IRREGULARIDADES = 'data/irregularidades.csv'

def carregar_equipamentos():
    if not os.path.exists(CSV_EQUIPAMENTOS):
        return []
    try:
        df = pd.read_csv(CSV_EQUIPAMENTOS, encoding='utf-8')
    except UnicodeDecodeError:
        df = pd.read_csv(CSV_EQUIPAMENTOS, encoding='ISO-8859-1')
    return sorted(set(df['EQUIPAMENTO'].dropna().str.strip().str.title()))

def carregar_irregularidades():
    if not os.path.exists(CSV_IRREGULARIDADES):
        return []
    try:
        df = pd.read_csv(CSV_IRREGULARIDADES, encoding='utf-8')
    except UnicodeDecodeError:
        df = pd.read_csv(CSV_IRREGULARIDADES, encoding='ISO-8859-1')
    return sorted(set(df['IRREGULARIDADES'].dropna().str.strip().str.title()))

def normalizar_nome(nome):
    nome = unicodedata.normalize('NFKD', nome).encode('ASCII', 'ignore').decode('ASCII')
    nome = re.sub(r'[^a-zA-Z0-9]', '_', nome)
    nome = re.sub(r'_+', '_', nome).strip('_').lower()
    return nome

def limpar_texto(texto):
    if not texto:
        return ''
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8')
    texto = re.sub(r'[^\w\s]', '', texto)  
    texto = texto.upper()
    return texto.strip()

def salvar_foto(file, latitude='', longitude='', ocupante=''):
    if file:
        # Normalizar coordenadas (substituir vírgulas por pontos)
        lat_str = str(latitude).strip().replace(',', '.')
        lon_str = str(longitude).strip().replace(',', '.')
        ocupante_norm = normalizar_nome(ocupante or 'ocupante')

        # Timestamp reduzido (YYMMDD_HHMMSS)
        timestamp = datetime.now().strftime('%y%m%d_%H%M%S')

        # Criar nome do arquivo
        filename = f"{lat_str}_{lon_str}_{ocupante_norm}_{timestamp}.jpg"
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        try:
            img = Image.open(file)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            try:
                resample_method = Image.Resampling.LANCZOS
            except AttributeError:
                resample_method = Image.LANCZOS

            max_width = 1280
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int((float(img.height) * ratio))
                img = img.resize((max_width, new_height), resample=resample_method)

            img.save(path, "JPEG", optimize=True, quality=70)
        except Exception as e:
            print("Erro ao comprimir imagem no backend:", e)
            file.save(path)

        return os.path.basename(path)
    return ''

@app.route('/')
def index():
    cidades_dict = carregar_cidades()
    tipos = carregar_tipo()
    equipamentos = sorted(set(carregar_equipamentos()))
    irregularidades = sorted(set(carregar_irregularidades()))
    return render_template('form.html', 
                           cidades=cidades_dict, 
                           tipos=tipos, 
                           equipamentos=equipamentos, 
                           irregularidades=irregularidades)

@app.route('/enviar', methods=['POST'])
def enviar():
    try:
        cidade = request.form.get('cidade')
        logradouro = limpar_texto(request.form.get('logradouro'))
        numero = request.form.get('numero')
        bairro = request.form.get('bairro')
        barramento = limpar_texto(request.form.get('barramento', ''))

        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        lat = lon = utm_x = utm_y = utm_zone_number = utm_zone_letter = None

        if latitude and longitude:
            try:
                lat = float(latitude)
                lon = float(longitude)
                utm_result = utm.from_latlon(lat, lon)
                utm_x, utm_y, utm_zone_number, utm_zone_letter = utm_result
            except Exception as e:
                print("Erro ao converter coordenadas UTM:", e)

        # Fotos do poste
        fotos_poste = request.files.getlist('foto_poste[]')
        fotos_poste_nomes = [
            salvar_foto(file, latitude, longitude, 'poste') for file in fotos_poste if file
        ]
        fotos_poste_str = ';'.join(fotos_poste_nomes)

        index = 0
        ocupantes_validos = 0

        while True:
            key = f'ocupante_nome_{index}'
            if key not in request.form:
                break

            ocupante = limpar_texto(request.form.get(f'ocupante_nome_{index}', ''))
            if not ocupante:
                index += 1
                continue

            nivel = limpar_texto(request.form.get(f'nivel_fixacao_{index}'))
            tipo_cabo = request.form.getlist(f'tipo_cabo_{index}[]')
            equipamento = request.form.getlist(f'equipamento_{index}[]')
            placa = limpar_texto(request.form.get(f'placa_identificacao_{index}', ''))
            irregularidades = request.form.getlist(f'irregularidades_{index}[]')

            tipo_cabo_outro = limpar_texto(request.form.get(f'tipo_cabo_outro_{index}', '').strip())
            if tipo_cabo_outro:
                tipo_cabo.append(tipo_cabo_outro)

            equipamento_outro = limpar_texto(request.form.get(f'equipamento_outro_{index}', '').strip())
            if equipamento_outro:
                equipamento.append(equipamento_outro)

            irregularidades_outro = limpar_texto(request.form.get(f'irregularidades_outro_{index}', '').strip())
            if irregularidades_outro:
                irregularidades.append(irregularidades_outro)

            tipo_cabo_str = ", ".join(tipo_cabo)
            equipamento_str = ", ".join(equipamento)
            irregularidades_str = ", ".join(irregularidades)

            ocupante_fotos = request.files.getlist(f'foto_ocupante_{index}[]')
            fotos_ocupante_nomes = [
                salvar_foto(file, latitude, longitude, ocupante) for file in ocupante_fotos if file
            ]
            fotos_ocupante_str = ';'.join(fotos_ocupante_nomes)

            salvar_no_banco(
                cidade, logradouro, numero, bairro, barramento, ocupante,
                nivel, tipo_cabo_str, equipamento_str, placa,
                irregularidades_str, fotos_ocupante_str, fotos_poste_str,
                lat, lon, utm_x, utm_y, utm_zone_number, utm_zone_letter
            )

            ocupantes_validos += 1
            index += 1

        return jsonify({"status": "ok", "ocupantes": ocupantes_validos})

    except Exception as e:
        print("Erro ao enviar dados:", e)
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

def salvar_no_banco(cidade, logradouro, numero, bairro, barramento, ocupante,
                    nivel, tipo_cabo, equipamento, placa,
                    irregularidades, fotos_ocupante, fotos_poste,
                    latitude, longitude, utm_x, utm_y, utm_zone_number, utm_zone_letter):
    try:
        latitude = float(latitude) if latitude is not None else None
        longitude = float(longitude) if longitude is not None else None
        utm_x = float(utm_x) if utm_x is not None else None
        utm_y = float(utm_y) if utm_y is not None else None
        utm_zone_number = int(utm_zone_number) if utm_zone_number is not None else None
        utm_zone_letter = str(utm_zone_letter) if utm_zone_letter is not None else None

        conexao = pymysql.connect(
            host='localhost',
            user='root',
            password='1234',
            database='app_fiscal'
        )
        cursor = conexao.cursor()
        query = """
            INSERT INTO registros (
                cidade, logradouro, numero, bairro, barramento, ocupante,
                nivel, tipo_cabo, equipamento, placa,
                irregularidades, fotos_ocupante, fotos_poste,
                latitude, longitude, utm_x, utm_y, utm_zone_number, utm_zone_letter
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        valores = (
            cidade, logradouro, numero, bairro, barramento, ocupante,
            nivel, tipo_cabo, equipamento, placa,
            irregularidades, fotos_ocupante, fotos_poste,
            latitude, longitude, utm_x, utm_y, utm_zone_number, utm_zone_letter
        )
        cursor.execute(query, valores)
        conexao.commit()
        cursor.close()
        conexao.close()
    except MySQLError as err:
        print(f"Erro ao inserir no banco: {err}")

@app.route('/admin')
def admin():
    try:
        conexao = pymysql.connect(
            host='localhost',
            user='root',
            password='1234',
            database='app_fiscal'
        )
        cursor = conexao.cursor(DictCursor)
        cursor.execute("SELECT * FROM registros")
        registros = cursor.fetchall()
        cursor.close()
        conexao.close()
    except MySQLError as err:
        registros = []
        print(f"Erro ao buscar dados do banco: {err}")

    return render_template('admin.html', registros=registros)

@app.route('/exportar_excel')
def exportar_excel():
    try:
        # Conectar e buscar dados do banco
        conexao = pymysql.connect(
            host='localhost',
            user='root',
            password='1234',
            database='app_fiscal'
        )
        cursor = conexao.cursor(DictCursor)
        cursor.execute("SELECT * FROM registros")
        registros = cursor.fetchall()
        cursor.close()
        conexao.close()

        df = pd.DataFrame(registros)

        contratos_path = 'data/PROVEDORES.xlsx'
        if os.path.exists(contratos_path):
            contratos_df = pd.read_excel(contratos_path)
            # Deixar nome padronizado
            contratos_df['provedor'] = contratos_df['provedor'].astype(str).str.upper().str.strip()
            # Criar um set para consulta rápida
            set_contratados = set(contratos_df['provedor'])
        else:
            set_contratados = set()

        df['ocupante_normalizado'] = df['ocupante'].astype(str).str.upper().str.strip()

        # Cria coluna 'clandestino': "Não" se estiver na planilha, "Sim" caso não esteja
        df['clandestino'] = df['ocupante_normalizado'].apply(lambda x: "Não" if x in set_contratados else "Sim")

        df.drop(columns=['ocupante_normalizado'], inplace=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Registros')
        output.seek(0)

        return send_file(
            output,
            download_name='registros.xlsx',
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return f"Erro ao exportar Excel: {e}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=('certificado.pem', 'chave.pem'), threaded=True)
