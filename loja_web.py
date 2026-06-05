#!/usr/bin/env python3
"""
Loja Online Didática
====================
python -m venv .
source bin/activate

Dependências:  
pip install pillow 
pip install qrcode

python loja_web.py

Funcionalidades:
  • Catálogo de produtos com busca
  • Carrinho de compras persistente
  • Pagamento via PIX com QR Code (fictício)
  • Rastreio de pedidos
  • Reclamações por pedido (aberta / concluída / recusada)
  • Mensagens comprador → administrador com confirmação de leitura
  • Edição de perfil (todos os usuários)
  • Admin: CRUD produtos, gerência de usuários, status de pedidos,
           respostas a reclamações, visualização de mensagens

Admin padrão: admin@loja.com / admin123
"""

import base64
import csv
import hashlib
import io
import json
import os
import random
import re
import socket
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode


# ═══════════════════════════════════════════════════════════════════════════════
#  MODEL
# ═══════════════════════════════════════════════════════════════════════════════

ARQ_USUARIOS     = "loja_usuarios.csv"
ARQ_PRODUTOS     = "loja_produtos.csv"
ARQ_CARRINHO     = "loja_carrinho.csv"
ARQ_PEDIDOS      = "loja_pedidos.csv"
ARQ_RECLAMACOES  = "loja_reclamacoes.csv"
ARQ_MENSAGENS    = "loja_mensagens.csv"
PASTA_FOTOS      = "FOTOS"

FOTO_W, FOTO_H = 400, 300

CAMPOS_USUARIOS    = ["id","nome","email","telefone","senha_hash","tipo"]
CAMPOS_PRODUTOS    = ["id","codigo","nome","descricao","preco","prazo_entrega"]
CAMPOS_CARRINHO    = ["id","usuario_id","produto_id","quantidade"]
CAMPOS_PEDIDOS     = ["id","codigo_pedido","codigo_rastreio","usuario_id",
                      "itens_json","total","status_pagamento","status_rastreio","data"]
CAMPOS_RECLAMACOES = ["id","pedido_id","usuario_id","texto","status","resposta","data"]
CAMPOS_MENSAGENS   = ["id","usuario_id","texto","lida","cancelada","data"]

# Listas em memória
usuarios:    list[dict] = []
produtos:    list[dict] = []
carrinho:    list[dict] = []
pedidos:     list[dict] = []
reclamacoes: list[dict] = []
mensagens:   list[dict] = []
sessoes:     dict[str,int] = {}

STATUS_PAGAMENTO   = ["Pendente","Pago"]
STATUS_RASTREIO    = ["Separando o pedido","Entregue à transportadora","Em entrega","Entregue"]
STATUS_RECLAMACAO  = ["Aberta","Concluída","Recusada"]
RASTREIO_BLOQUEIO  = {"Entregue à transportadora","Em entrega","Entregue"}

RE_EMAIL = re.compile(r"^[\w.\+\-]+@[\w\-]+\.[a-z]{2,}$", re.I)
RE_TEL   = re.compile(r"^[\d\s()\-\+]{7,20}$")

def ok_email(e): return bool(RE_EMAIL.match(e.strip()))
def ok_tel(t):   return bool(RE_TEL.match(t.strip()))
def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()
def prox_id(lst): return max((x["id"] for x in lst), default=0) + 1
def buscar_id(lst, cid): return next((x for x in lst if x["id"]==cid), None)
def gerar_codigo_produto():  return "PRD-"+str(random.randint(10000,99999))
def gerar_codigo_pedido():   return "PED-"+uuid.uuid4().hex[:8].upper()
def gerar_codigo_rastreio(): return "BR"+uuid.uuid4().hex[:10].upper()+"BR"
def agora(): return datetime.now().strftime("%d/%m/%Y %H:%M")

def extrair_dias(prazo_str: str) -> str:
    """Extrai o número de dias de uma string como '7 dias úteis' → '7'."""
    m = re.match(r"^\s*(\d+)", str(prazo_str))
    return m.group(1) if m else "5"

# ── Foto ──────────────────────────────────────────────────────────────────────

def caminho_foto(cod): return os.path.join(PASTA_FOTOS, f"{cod}.png")
def foto_existe(cod):  return os.path.exists(caminho_foto(cod))

FORMATOS_ACEITOS = {"JPEG","PNG","GIF","WEBP","BMP","TIFF"}

def processar_foto(dados: bytes) -> bytes | None:
    try:
        img = Image.open(io.BytesIO(dados))
        if img.format not in FORMATOS_ACEITOS: return None
        img = img.convert("RGB")
        img = ImageOps.fit(img, (FOTO_W,FOTO_H), Image.LANCZOS)
        buf = io.BytesIO(); img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception: return None

def salvar_foto(cod, dados_png):
    os.makedirs(PASTA_FOTOS, exist_ok=True)
    with open(caminho_foto(cod),"wb") as f: f.write(dados_png)

# ── QR Code ───────────────────────────────────────────────────────────────────

def gerar_qrcode_b64(texto: str) -> str:
    qr = qrcode.QRCode(version=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=8, border=3)
    qr.add_data(texto); qr.make(fit=True)
    img = qr.make_image(fill_color="#0f766e", back_color="white")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

# ── Persistência CSV ──────────────────────────────────────────────────────────

def _salvar(lst, arq, campos):
    with open(arq,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=campos); w.writeheader(); w.writerows(lst)

def _carregar(arq, campos, conv=None):
    if not os.path.exists(arq): return []
    res=[]
    with open(arq,newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            item={c: row.get(c,"") for c in campos}
            if conv:
                for campo,fn in conv.items():
                    try: item[campo]=fn(item[campo])
                    except: pass
            res.append(item)
    return res

def salv_usuarios():    _salvar(usuarios,    ARQ_USUARIOS,    CAMPOS_USUARIOS)
def salv_produtos():    _salvar(produtos,    ARQ_PRODUTOS,    CAMPOS_PRODUTOS)
def salv_carrinho():    _salvar(carrinho,    ARQ_CARRINHO,    CAMPOS_CARRINHO)
def salv_pedidos():     _salvar(pedidos,     ARQ_PEDIDOS,     CAMPOS_PEDIDOS)
def salv_reclamacoes(): _salvar(reclamacoes, ARQ_RECLAMACOES, CAMPOS_RECLAMACOES)
def salv_mensagens():   _salvar(mensagens,   ARQ_MENSAGENS,   CAMPOS_MENSAGENS)

def carregar_tudo():
    global usuarios,produtos,carrinho,pedidos,reclamacoes,mensagens
    usuarios    = _carregar(ARQ_USUARIOS,    CAMPOS_USUARIOS,    {"id":int})
    produtos    = _carregar(ARQ_PRODUTOS,    CAMPOS_PRODUTOS,    {"id":int,"preco":float})
    carrinho    = _carregar(ARQ_CARRINHO,    CAMPOS_CARRINHO,
                            {"id":int,"usuario_id":int,"produto_id":int,"quantidade":int})
    pedidos     = _carregar(ARQ_PEDIDOS,     CAMPOS_PEDIDOS,     {"id":int,"usuario_id":int,"total":float})
    reclamacoes = _carregar(ARQ_RECLAMACOES, CAMPOS_RECLAMACOES,
                            {"id":int,"pedido_id":int,"usuario_id":int})
    mensagens   = _carregar(ARQ_MENSAGENS,   CAMPOS_MENSAGENS,   {"id":int,"usuario_id":int})

def inicializar_dados():
    os.makedirs(PASTA_FOTOS, exist_ok=True)
    carregar_tudo()
    if not any(u["tipo"]=="admin" for u in usuarios):
        usuarios.append({"id":1,"nome":"Administrador","email":"admin@loja.com",
                         "telefone":"(11) 99999-0000","senha_hash":hash_senha("admin123"),
                         "tipo":"admin"})
        salv_usuarios(); print("  👤  Admin padrão: admin@loja.com / admin123")
    if not produtos:
        demos = [(1,"PRD-10001","Camiseta Básica",
                  "Camiseta 100% algodão, disponível em várias cores.",49.90,"5 dias úteis"),
                 (2,"PRD-10002","Caneca Térmica 500ml",
                  "Caneca de aço inox com tampa hermética.",89.90,"7 dias úteis"),
                 (3,"PRD-10003","Mochila para Notebook",
                  "Mochila impermeável para notebook até 15.6\".",199.90,"10 dias úteis")]
        for pid,cod,nome,desc,preco,prazo in demos:
            produtos.append({"id":pid,"codigo":cod,"nome":nome,
                             "descricao":desc,"preco":preco,"prazo_entrega":prazo})
        salv_produtos(); print("  📦  3 produtos demo criados.")

# ── Sessão / Carrinho ─────────────────────────────────────────────────────────

def criar_sessao(uid):
    tok=uuid.uuid4().hex; sessoes[tok]=uid; return tok
def usuario_logado(tok):
    if not tok: return None
    uid=sessoes.get(tok)
    return buscar_id(usuarios,uid) if uid else None
def destruir_sessao(tok): sessoes.pop(tok,None)
def itens_do_carrinho(uid): return [c for c in carrinho if c["usuario_id"]==uid]

# ── Helpers de reclamações / mensagens ────────────────────────────────────────

def reclamacao_do_pedido(pedido_id):
    return next((r for r in reclamacoes if r["pedido_id"]==pedido_id), None)

def mensagens_do_usuario(uid):
    return sorted([m for m in mensagens if m["usuario_id"]==uid],
                  key=lambda x: x["id"], reverse=True)

def n_mensagens_nao_lidas():
    """Conta mensagens ativas não lidas (para badge no painel admin)."""
    return sum(1 for m in mensagens if m["lida"]=="0" and m["cancelada"]=="0")

def n_reclamacoes_abertas():
    return sum(1 for r in reclamacoes if r["status"]=="Aberta")

# ── Parser multipart ──────────────────────────────────────────────────────────

def _parse_multipart(body:bytes, boundary:str) -> dict:
    result:dict={}; sep=("--"+boundary).encode()
    for parte in body.split(sep)[1:]:
        if parte[:2]==b"--": break
        if   parte[:2]==b"\r\n": parte=parte[2:]
        elif parte[:1]==b"\n":   parte=parte[1:]
        if   b"\r\n\r\n" in parte: hdr,cont=parte.split(b"\r\n\r\n",1)
        elif b"\n\n"      in parte: hdr,cont=parte.split(b"\n\n",1)
        else: continue
        if   cont.endswith(b"\r\n"): cont=cont[:-2]
        elif cont.endswith(b"\n"):   cont=cont[:-1]
        h=hdr.decode("utf-8",errors="replace")
        m=re.search(r'Content-Disposition:[^\r\n]*\bname="([^"]*)"',h,re.I)
        if not m: continue
        mf=re.search(r'\bfilename="([^"]*)"',h,re.I)
        if mf: result[m.group(1)]=(mf.group(1),cont)
        else:  result[m.group(1)]=cont.decode("utf-8",errors="replace")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — CSS
# ═══════════════════════════════════════════════════════════════════════════════

_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#f0f4f8;--card:#fff;--primary:#0d9488;--prim-dark:#0f766e;
    --danger:#dc2626;--warn:#d97706;--text:#1e293b;--muted:#64748b;
    --border:#e2e8f0;--radius:8px;
    --font:'IBM Plex Sans',sans-serif;--mono:'IBM Plex Mono',monospace;
  }
  body{font-family:var(--font);background:var(--bg);color:var(--text);min-height:100vh}
  header{background:var(--primary);color:#fff;padding:14px 32px;display:flex;align-items:center;gap:14px}
  header .logo{font-size:1.4rem;font-weight:700}
  header .sub{font-size:.78rem;opacity:.75;margin-top:1px}
  nav{background:var(--card);border-bottom:1px solid var(--border);
      padding:0 24px;display:flex;align-items:stretch;gap:0;flex-wrap:wrap}
  nav a{display:inline-block;padding:11px 12px;font-size:.82rem;font-weight:600;
        color:var(--muted);text-decoration:none;border-bottom:3px solid transparent;white-space:nowrap}
  nav a:hover{color:var(--primary)}
  nav a.ativo{color:var(--primary);border-bottom-color:var(--primary)}
  nav .nb{background:var(--danger);color:#fff;border-radius:99px;font-size:.65rem;
          padding:1px 5px;margin-left:3px;vertical-align:middle}
  nav .sep{flex:1}
  main{padding:28px 32px;max-width:1080px;margin:0 auto}
  h2{font-size:1.15rem;font-weight:600;margin-bottom:20px}
  h3{font-size:.95rem;font-weight:600;margin-bottom:12px}
  .msg{padding:10px 16px;border-radius:var(--radius);font-size:.875rem;margin-bottom:18px}
  .ok  {background:#dcfce7;color:#166534;border:1px solid #bbf7d0}
  .err {background:#fee2e2;color:#991b1b;border:1px solid #fecaca}
  .inf {background:#e0f2fe;color:#0369a1;border:1px solid #bae6fd}
  .warn{background:#fef3c7;color:#92400e;border:1px solid #fde68a}
  .tbl-wrap{overflow-x:auto;background:var(--card);border-radius:var(--radius);
             border:1px solid var(--border);margin-bottom:20px}
  table{width:100%;border-collapse:collapse;font-size:.875rem}
  thead th{background:#f8fafc;padding:10px 14px;text-align:left;font-size:.72rem;
            font-weight:600;text-transform:uppercase;letter-spacing:.07em;
            color:var(--muted);border-bottom:1px solid var(--border)}
  tbody td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
  tbody tr:last-child td{border-bottom:none}
  tbody tr:hover{background:#f8fafc}
  .vazio{text-align:center;color:var(--muted);padding:32px 16px}
  .badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:.71rem;font-weight:600}
  .b-admin    {background:#e0f2fe;color:#0369a1}
  .b-comprador{background:#fef3c7;color:#92400e}
  .b-pendente {background:#fef3c7;color:#92400e}
  .b-pago     {background:#dcfce7;color:#166534}
  .b-separando{background:#e0f2fe;color:#0369a1}
  .b-transport{background:#f3e8ff;color:#7e22ce}
  .b-entrega  {background:#fff7ed;color:#c2410c}
  .b-entregue {background:#dcfce7;color:#166534}
  .b-aguard   {background:#fef3c7;color:#92400e}
  .b-aberta   {background:#fee2e2;color:#991b1b}
  .b-concluida{background:#dcfce7;color:#166534}
  .b-recusada {background:#f1f5f9;color:#64748b}
  .b-lida     {background:#dcfce7;color:#166534}
  .b-nao-lida {background:#fef3c7;color:#92400e}
  .b-cancelada{background:#f1f5f9;color:#64748b}
  .btn{display:inline-block;padding:8px 18px;border-radius:var(--radius);font-size:.82rem;
       font-weight:600;text-decoration:none;cursor:pointer;
       border:1px solid transparent;font-family:var(--font)}
  .btn-primary{background:var(--primary);color:#fff}
  .btn-primary:hover{background:var(--prim-dark)}
  .btn-danger{background:var(--danger);color:#fff}
  .btn-danger:hover{background:#b91c1c}
  .btn-sec{background:var(--card);color:var(--text);border-color:var(--border)}
  .btn-sec:hover{background:var(--bg)}
  .btn-warn{background:var(--warn);color:#fff}
  .btn-warn:hover{background:#b45309}
  .btn-sm{padding:4px 10px;font-size:.74rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:24px}
  .form-card{max-width:520px}
  .field{margin-bottom:15px}
  .field label{display:block;font-size:.79rem;font-weight:600;color:var(--muted);
               text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
  .field input,.field textarea,.field select{
    width:100%;padding:9px 12px;border:1px solid var(--border);
    border-radius:var(--radius);font-size:.875rem;font-family:var(--font);
    color:var(--text);background:var(--bg)}
  .field input[type=file]{padding:6px 10px;cursor:pointer}
  .field input[type=number]{width:120px}
  .field input:focus,.field textarea:focus,.field select:focus{
    outline:none;border-color:var(--primary);background:#fff;
    box-shadow:0 0 0 3px rgba(13,148,136,.12)}
  .field textarea{min-height:90px;resize:vertical}
  .field small{display:block;margin-top:4px;font-size:.75rem;color:var(--muted)}
  .row-btn{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}
  .foto-thumb{width:200px;height:150px;object-fit:cover;
              border-radius:var(--radius);border:2px solid var(--border);
              display:block;margin-bottom:8px}
  .sem-foto{width:200px;height:150px;border-radius:var(--radius);
            border:2px dashed var(--border);display:flex;align-items:center;
            justify-content:center;color:var(--muted);font-size:.8rem;
            background:var(--bg);margin-bottom:8px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:18px;margin-bottom:20px}
  .prod-card{background:var(--card);border:1px solid var(--border);
             border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column}
  .prod-card img{width:100%;height:175px;object-fit:cover}
  .prod-card .sem-img{width:100%;height:175px;display:flex;align-items:center;
                      justify-content:center;background:#f1f5f9;color:var(--muted);font-size:.8rem}
  .prod-card .pinfo{padding:14px;flex:1;display:flex;flex-direction:column;gap:5px}
  .prod-card .pnome{font-weight:600;font-size:.9rem}
  .prod-card .pcod{font-family:var(--mono);font-size:.72rem;color:var(--muted)}
  .prod-card .pdesc{font-size:.78rem;color:var(--muted);flex:1;line-height:1.4}
  .prod-card .ppreco{font-size:1.1rem;font-weight:700;color:var(--primary);margin-top:4px}
  .prod-card .pprazo{font-size:.73rem;color:var(--muted)}
  .prod-card .pfoot{padding:0 14px 14px}
  .cart-item{display:flex;align-items:center;gap:14px;
             padding:12px 0;border-bottom:1px solid var(--border)}
  .cart-item:last-child{border-bottom:none}
  .cart-item img{width:64px;height:64px;object-fit:cover;border-radius:6px;flex-shrink:0}
  .cart-item .ci{flex:1}
  .cart-item .cnome{font-weight:600;font-size:.9rem}
  .cart-item .cdet{font-size:.78rem;color:var(--muted);margin-top:2px}
  .pix-box{background:#f0fdf4;border:2px solid #86efac;border-radius:var(--radius);
           padding:28px;text-align:center;max-width:500px;margin:0 auto 24px}
  .pix-valor{font-size:2.2rem;font-weight:700;color:var(--primary);margin:10px 0}
  .pix-qr{margin:16px auto;display:block;border-radius:8px;border:3px solid #86efac;max-width:220px}
  .pix-codigo{font-family:var(--mono);font-size:.72rem;background:#fff;
              border:1px solid var(--border);padding:10px;border-radius:6px;
              word-break:break-all;margin:12px 0;text-align:left;line-height:1.5}
  /* Mensagens */
  .msg-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
            padding:14px;margin-bottom:12px}
  .msg-card .mc-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .msg-card .mc-body{font-size:.875rem;color:var(--text);line-height:1.5;
                     white-space:pre-wrap;word-break:break-word}
  .msg-card .mc-foot{margin-top:8px;display:flex;gap:8px;align-items:center}
  /* Reclamação */
  .reclam-box{background:#fff7ed;border:1px solid #fde68a;border-radius:var(--radius);
              padding:14px;margin-top:10px}
  .reclam-box .rb-head{font-weight:600;font-size:.85rem;margin-bottom:6px}
  .reclam-box .rb-body{font-size:.85rem;color:var(--text);white-space:pre-wrap;word-break:break-word}
  .reclam-box .rb-resp{margin-top:8px;padding:8px;background:#f0fdf4;border-radius:4px;
                       font-size:.82rem;color:#166534}
  .busca{display:flex;gap:8px;margin-bottom:20px}
  .busca input{flex:1;padding:9px 12px;border:1px solid var(--border);
               border-radius:var(--radius);font-size:.875rem;font-family:var(--font);background:var(--card)}
  .stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px;margin-bottom:28px}
  .stat-card{background:var(--card);border:1px solid var(--border);
             border-radius:var(--radius);padding:20px;text-align:center}
  .stat-icon{font-size:2rem}
  .stat-num{font-size:1.8rem;font-weight:700;margin:6px 0 2px}
  .stat-label{font-size:.78rem;color:var(--muted)}
  .mono{font-family:var(--mono);font-size:.8rem;color:var(--muted)}
  hr.sep-form{border:none;border-top:1px solid var(--border);margin:20px 0}
  .dias-field{display:flex;align-items:center;gap:8px}
  .dias-field input{width:90px!important}
  .dias-field span{font-size:.875rem;color:var(--muted);font-weight:600}
  footer{text-align:center;font-size:.75rem;color:var(--muted);
         padding:24px;margin-top:40px;font-family:var(--mono)}
</style>
"""

# ── Helpers HTML ──────────────────────────────────────────────────────────────

def _h(txt):
    return str(txt).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def _msg(texto,tipo="ok"):
    return f'<div class="msg {tipo}">{_h(texto)}</div>' if texto else ""

def _badge_tipo(tipo):
    return ('<span class="badge b-admin">Admin</span>' if tipo=="admin"
            else '<span class="badge b-comprador">Comprador</span>')

def _badge_pag(s):
    c="b-pago" if s=="Pago" else "b-pendente"
    return f'<span class="badge {c}">{_h(s)}</span>'

def _badge_rat(s, pag=None):
    if pag=="Pendente": return '<span class="badge b-aguard">⏳ Aguardando Pagamento</span>'
    m={"Separando o pedido":"b-separando","Entregue à transportadora":"b-transport",
       "Em entrega":"b-entrega","Entregue":"b-entregue"}
    return f'<span class="badge {m.get(s,"b-separando")}">{_h(s)}</span>'

def _badge_recl(s):
    m={"Aberta":"b-aberta","Concluída":"b-concluida","Recusada":"b-recusada"}
    return f'<span class="badge {m.get(s,"b-aberta")}">{_h(s)}</span>'

def _badge_msg(lida, cancelada):
    if cancelada=="1": return '<span class="badge b-cancelada">✕ Cancelada</span>'
    if lida=="1":      return '<span class="badge b-lida">✅ Lida</span>'
    return '<span class="badge b-nao-lida">👁 Aguardando leitura</span>'

def _nav_html(usuario):
    if not usuario:
        return ('<nav><a href="/catalogo">🛍 Catálogo</a>'
                '<a href="/login">🔑 Entrar</a>'
                '<a href="/registrar">📝 Registrar-se</a></nav>')
    if usuario["tipo"]=="admin":
        nr=n_reclamacoes_abertas(); nm=n_mensagens_nao_lidas()
        nb_r=f'<span class="nb">{nr}</span>' if nr else ""
        nb_m=f'<span class="nb">{nm}</span>' if nm else ""
        return (f'<nav>'
                f'<a href="/admin">🏠 Painel</a>'
                f'<a href="/admin/produtos">📦 Produtos</a>'
                f'<a href="/admin/usuarios">👥 Usuários</a>'
                f'<a href="/admin/pedidos">📋 Pedidos</a>'
                f'<a href="/admin/reclamacoes">⚠ Reclamações{nb_r}</a>'
                f'<a href="/admin/mensagens">💬 Mensagens{nb_m}</a>'
                f'<span class="sep"></span>'
                f'<a href="/meu-perfil">⚙ Perfil</a>'
                f'<a href="/logout">🚪 Sair</a>'
                f'</nav>')
    n=len(itens_do_carrinho(usuario["id"]))
    lbl=f"🛒 Carrinho ({n})" if n else "🛒 Carrinho"
    return (f'<nav>'
            f'<a href="/catalogo">🛍 Catálogo</a>'
            f'<a href="/carrinho">{lbl}</a>'
            f'<a href="/meus-pedidos">📦 Meus Pedidos</a>'
            f'<a href="/minhas-mensagens">💬 Mensagens</a>'
            f'<span class="sep"></span>'
            f'<a href="/meu-perfil">⚙ Perfil</a>'
            f'<a href="/logout">🚪 Sair</a>'
            f'</nav>')

def _layout(titulo, conteudo, usuario=None):
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_h(titulo)} — Loja Didática</title>{_CSS}
</head>
<body>
  <header>
    <div><div class="logo">🛒 Loja Didática</div><div class="sub">Projeto educacional</div></div>
  </header>
  {_nav_html(usuario)}
  <main>{conteudo}</main>
  <footer>loja_web.py · apenas fins educacionais</footer>
</body>
</html>"""

def _img_prod(cod, cls="foto-thumb"):
    if foto_existe(cod): return f'<img class="{cls}" src="/fotos/{_h(cod)}.png" alt="foto">'
    return '<div class="sem-foto">📷 Sem foto</div>'


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Autenticação
# ═══════════════════════════════════════════════════════════════════════════════

def html_login(erro=""):
    corpo=(f"<h2>🔑 Entrar na Loja</h2>{_msg(erro,'err')}"
           f'<div class="card form-card"><form method="post" action="/login">'
           f'<div class="field"><label>E-mail</label>'
           f'<input type="email" name="email" required autofocus placeholder="seu@email.com"></div>'
           f'<div class="field"><label>Senha</label>'
           f'<input type="password" name="senha" required placeholder="••••••••"></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">Entrar</button>'
           f'<a class="btn btn-sec" href="/registrar">Criar conta</a>'
           f'</div></form></div>')
    return _layout("Login",corpo)

def html_registrar(campos={},erro=""):
    v=lambda k:_h(campos.get(k,""))
    corpo=(f"<h2>📝 Criar Conta de Comprador</h2>{_msg(erro,'err')}"
           f'<div class="card form-card"><form method="post" action="/registrar">'
           f'<div class="field"><label>Nome completo</label><input name="nome" value="{v("nome")}" required placeholder="João da Silva"></div>'
           f'<div class="field"><label>E-mail</label><input type="email" name="email" value="{v("email")}" required placeholder="joao@email.com"></div>'
           f'<div class="field"><label>Confirmar e-mail</label><input type="email" name="email2" value="{v("email2")}" required placeholder="repita o e-mail"></div>'
           f'<div class="field"><label>Telefone</label><input name="telefone" value="{v("telefone")}" required placeholder="(11) 99999-9999"></div>'
           f'<div class="field"><label>Senha (mínimo 6 caracteres)</label><input type="password" name="senha" required placeholder="••••••••"></div>'
           f'<div class="field"><label>Confirmar senha</label><input type="password" name="senha2" required placeholder="repita a senha"></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">Criar Conta</button>'
           f'<a class="btn btn-sec" href="/login">Já tenho conta</a>'
           f'</div></form></div>')
    return _layout("Registrar-se",corpo)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Perfil
# ═══════════════════════════════════════════════════════════════════════════════

def html_editar_perfil(usuario, alvo, campos={}, erro="", msg="", admin_editando=False):
    v=lambda k,fb="":_h(campos.get(k, alvo.get(k,fb)))
    action=f"/admin/usuario/editar?id={alvo['id']}" if admin_editando else "/meu-perfil"
    titulo=f"✏ Editar: {_h(alvo['nome'])}" if admin_editando else "⚙ Meu Perfil"
    tipo_field=""
    if admin_editando:
        opts="".join(f'<option value="{t}" {"selected" if alvo["tipo"]==t else ""}>{t.capitalize()}</option>'
                     for t in ["comprador","admin"])
        tipo_field=f'<div class="field"><label>Tipo de usuário</label><select name="tipo">{opts}</select></div>'
    corpo=(f"<h2>{titulo}</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<div class="card form-card"><form method="post" action="{action}">'
           f'<h3>Dados pessoais</h3>'
           f'<div class="field"><label>Nome completo</label><input name="nome" value="{v("nome")}" required></div>'
           f'<div class="field"><label>E-mail</label><input type="email" name="email" value="{v("email")}" required></div>'
           f'<div class="field"><label>Confirmar e-mail</label><input type="email" name="email2" value="{v("email2",v("email"))}" required></div>'
           f'<div class="field"><label>Telefone</label><input name="telefone" value="{v("telefone")}" required placeholder="(11) 99999-9999"></div>'
           f'{tipo_field}'
           f'<hr class="sep-form">'
           f'<h3>Alterar senha <small style="font-weight:400;font-size:.8rem;color:var(--muted)">(deixe em branco para não alterar)</small></h3>'
           f'<div class="field"><label>Nova senha (mínimo 6 caracteres)</label><input type="password" name="senha" placeholder="••••••••"></div>'
           f'<div class="field"><label>Confirmar nova senha</label><input type="password" name="senha2" placeholder="repita a nova senha"></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">💾 Salvar</button>'
           f'<a class="btn btn-sec" href="javascript:history.back()">Cancelar</a>'
           f'</div></form></div>')
    return _layout(titulo,corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Catálogo / Carrinho / PIX
# ═══════════════════════════════════════════════════════════════════════════════

def html_catalogo(usuario, lista, busca="", msg=""):
    cards=""
    for p in lista:
        fhtml=(f'<img src="/fotos/{_h(p["codigo"])}.png" alt="{_h(p["nome"])}">'
               if foto_existe(p["codigo"]) else '<div class="sem-img">📷 Sem foto</div>')
        if usuario and usuario["tipo"]=="comprador":
            acao=(f'<div class="pfoot"><form method="post" action="/carrinho/adicionar">'
                  f'<input type="hidden" name="produto_id" value="{p["id"]}">'
                  f'<button class="btn btn-primary" style="width:100%" type="submit">+ Carrinho</button>'
                  f'</form></div>')
        elif not usuario:
            acao=(f'<div class="pfoot"><a class="btn btn-sec" '
                  f'style="width:100%;text-align:center;display:block" href="/login">Entre para comprar</a></div>')
        else:
            acao=""
        cards+=(f'<div class="prod-card">{fhtml}'
                f'<div class="pinfo">'
                f'<div class="pnome">{_h(p["nome"])}</div>'
                f'<div class="pcod">{_h(p["codigo"])}</div>'
                f'<div class="pdesc">{_h(p["descricao"])}</div>'
                f'<div class="ppreco">R$ {float(p["preco"]):.2f}</div>'
                f'<div class="pprazo">⏱ {_h(p["prazo_entrega"])}</div>'
                f'</div>{acao}</div>')
    if not cards: cards='<p style="color:var(--muted);padding:20px 0">Nenhum produto encontrado.</p>'
    limpar=f'<a class="btn btn-sec" href="/catalogo">✕ Limpar</a>' if busca else ""
    corpo=(f"<h2>🛍 Catálogo</h2>{_msg(msg,'ok')}"
           f'<form class="busca" method="get" action="/catalogo">'
           f'<input name="q" value="{_h(busca)}" placeholder="Buscar por nome ou descrição...">'
           f'<button class="btn btn-primary" type="submit">Buscar</button>{limpar}'
           f'</form><div class="grid">{cards}</div>')
    return _layout("Catálogo",corpo,usuario)

def html_carrinho(usuario, msg="", erro=""):
    itens=itens_do_carrinho(usuario["id"]); linhas=""; total=0.0
    for item in itens:
        p=buscar_id(produtos,item["produto_id"])
        if not p: continue
        sub=float(p["preco"])*item["quantidade"]; total+=sub
        ihtml=(f'<img src="/fotos/{_h(p["codigo"])}.png" alt="">'
               if foto_existe(p["codigo"])
               else '<div style="width:64px;height:64px;background:#f1f5f9;border-radius:6px;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:1.4rem">📷</div>')
        linhas+=(f'<div class="cart-item">{ihtml}'
                 f'<div class="ci"><div class="cnome">{_h(p["nome"])}</div>'
                 f'<div class="cdet">{_h(p["codigo"])} · R$ {float(p["preco"]):.2f} × {item["quantidade"]} = <strong>R$ {sub:.2f}</strong></div>'
                 f'<div class="cdet">⏱ {_h(p["prazo_entrega"])}</div></div>'
                 f'<form method="post" action="/carrinho/remover">'
                 f'<input type="hidden" name="item_id" value="{item["id"]}">'
                 f'<button class="btn btn-danger btn-sm" type="submit">Remover</button></form></div>')
    if not linhas: linhas='<p style="color:var(--muted);padding:20px 0">Carrinho vazio. <a href="/catalogo">Ver catálogo →</a></p>'
    rodape=""
    if itens:
        rodape=(f'<div style="border-top:2px solid var(--border);padding-top:16px;margin-top:8px;text-align:right">'
                f'<div style="font-size:1.2rem;font-weight:700;margin-bottom:16px">Total: R$ {total:.2f}</div>'
                f'<form method="post" action="/carrinho/finalizar">'
                f'<button class="btn btn-primary" type="submit">💳 Finalizar e Gerar PIX</button>'
                f'</form></div>')
    corpo=(f"<h2>🛒 Meu Carrinho</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<div class="card">{linhas}</div>{rodape}')
    return _layout("Carrinho",corpo,usuario)

def html_pix(usuario, pedido):
    try:    itens_d=json.loads(pedido.get("itens_json","[]"))
    except: itens_d=[]
    linhas="".join(
        f"<tr><td>{_h(i.get('nome',''))}</td><td style='text-align:center'>{i.get('qtd',1)}×</td>"
        f"<td style='text-align:right'>R$ {float(i.get('preco',0)):.2f}</td>"
        f"<td style='text-align:right;font-weight:600'>R$ {float(i.get('subtotal',0)):.2f}</td></tr>"
        for i in itens_d)
    v=f"{float(pedido['total']):.2f}"
    chave=(f"00020126580014BR.GOV.BCB.PIX0136{pedido['codigo_pedido']}"
           f"520400005303986 54{len(v):02d}{v}5802BR5913LojaDidatica"
           f"6008SaoPaulo62200516{pedido['codigo_rastreio']}6304ABCD")
    qr_b64=gerar_qrcode_b64(chave)
    qr_img=f'<img class="pix-qr" src="data:image/png;base64,{qr_b64}" alt="QR Code PIX" width="200">'
    corpo=(f"<h2>💳 Pagamento via PIX</h2>"
           f'<div class="pix-box">'
           f'<div style="font-size:2.5rem">📱</div>'
           f'<h3 style="margin:8px 0 2px">Escaneie o QR Code ou copie o código</h3>'
           f'<div class="pix-valor">R$ {float(pedido["total"]):.2f}</div>'
           f'{qr_img}'
           f'<details style="text-align:left;margin-top:8px">'
           f'<summary style="cursor:pointer;font-size:.8rem;color:var(--muted);text-align:center">Mostrar código copia-e-cola</summary>'
           f'<div class="pix-codigo">{_h(chave)}</div></details>'
           f'<p style="font-size:.78rem;color:var(--muted);margin-top:12px">⚠ PIX <strong>fictício</strong> — apenas fins didáticos.</p>'
           f'</div>'
           f'<div class="card" style="max-width:520px;margin:0 auto">'
           f'<h3>📋 Resumo do Pedido</h3>'
           f'<div class="tbl-wrap" style="margin-top:12px">'
           f'<table><thead><tr><th>Produto</th><th>Qtd</th><th>Preço</th><th>Subtotal</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>'
           f'<p style="margin-top:10px"><strong>Pedido:</strong> <span class="mono">{_h(pedido["codigo_pedido"])}</span></p>'
           f'<p style="margin:6px 0"><strong>Rastreio:</strong> <span class="mono">{_h(pedido["codigo_rastreio"])}</span></p>'
           f'<p><strong>Pagamento:</strong> {_badge_pag(pedido["status_pagamento"])}</p>'
           f'<p style="margin-top:6px"><strong>Entrega:</strong> {_badge_rat(pedido["status_rastreio"],pedido["status_pagamento"])}</p>'
           f'<div class="row-btn" style="margin-top:18px">'
           f'<a class="btn btn-primary" href="/meus-pedidos">Ver pedidos</a>'
           f'<a class="btn btn-sec" href="/catalogo">Continuar comprando</a>'
           f'</div></div>')
    return _layout("PIX",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Meus Pedidos + Reclamações
# ═══════════════════════════════════════════════════════════════════════════════

def html_meus_pedidos(usuario, msg="", erro=""):
    meus=sorted([p for p in pedidos if p["usuario_id"]==usuario["id"]],
                key=lambda x:x["id"],reverse=True)
    if not meus:
        corpo=("<h2>📦 Meus Pedidos</h2>"
               f'{_msg(msg,"ok")}{_msg(erro,"err")}'
               '<p style="color:var(--muted)">Você ainda não realizou nenhum pedido. '
               '<a href="/catalogo">Ver catálogo →</a></p>')
        return _layout("Meus Pedidos",corpo,usuario)

    linhas=""
    for ped in meus:
        # Reclamação vinculada
        recl=reclamacao_do_pedido(ped["id"])
        if recl:
            btn_recl=f'{_badge_recl(recl["status"])} <a class="btn btn-sec btn-sm" href="/meu-pedido/reclamacao?id={ped["id"]}">Ver</a>'
        else:
            btn_recl=f'<a class="btn btn-sec btn-sm" href="/meu-pedido/reclamacao?id={ped["id"]}">⚠ Reclamar</a>'

        btn_pagar=f'<a class="btn btn-warn btn-sm" href="/pix?id={ped["id"]}">💳 Pagar</a> ' if ped["status_pagamento"]=="Pendente" else ""
        btn_cancel=f'<a class="btn btn-danger btn-sm" href="/meu-pedido/excluir?id={ped["id"]}">✕ Cancelar</a> ' if ped["status_rastreio"] not in RASTREIO_BLOQUEIO else ""

        linhas+=(f"<tr>"
                 f"<td class='mono' style='white-space:nowrap'>{_h(ped['codigo_pedido'])}</td>"
                 f"<td class='mono'>{_h(ped['codigo_rastreio'])}</td>"
                 f"<td>R$ {float(ped['total']):.2f}</td>"
                 f"<td>{_badge_pag(ped['status_pagamento'])}</td>"
                 f"<td>{_badge_rat(ped['status_rastreio'],ped['status_pagamento'])}</td>"
                 f"<td>{_h(ped['data'])}</td>"
                 f"<td style='white-space:nowrap'>{btn_pagar}{btn_cancel}{btn_recl}</td>"
                 f"</tr>")

    corpo=(f"<h2>📦 Meus Pedidos</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<div class="tbl-wrap"><table>'
           f'<thead><tr><th>Código</th><th>Rastreio</th><th>Total</th>'
           f'<th>Pagamento</th><th>Entrega</th><th>Data</th><th>Ações</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>')
    return _layout("Meus Pedidos",corpo,usuario)


def html_reclamacao(usuario, pedido, recl=None, erro="", msg=""):
    """Formulário/visualização de reclamação de um pedido."""
    cod=_h(pedido["codigo_pedido"])
    if recl:
        # Já existe — mostra detalhes
        resposta_html=""
        if recl.get("resposta","").strip():
            resposta_html=f'<div class="rb-resp">📩 <strong>Resposta da loja:</strong> {_h(recl["resposta"])}</div>'
        corpo=(f"<h2>⚠ Reclamação do Pedido {cod}</h2>"
               f'{_msg(msg,"ok")}{_msg(erro,"err")}'
               f'<div class="reclam-box">'
               f'<div class="rb-head">{_badge_recl(recl["status"])} — {_h(recl["data"])}</div>'
               f'<div class="rb-body">{_h(recl["texto"])}</div>'
               f'{resposta_html}'
               f'</div>'
               f'<br><a class="btn btn-sec" href="/meus-pedidos">← Voltar aos pedidos</a>')
    else:
        # Formulário para abrir reclamação
        corpo=(f"<h2>⚠ Abrir Reclamação — Pedido {cod}</h2>"
               f'{_msg(erro,"err")}'
               f'<div class="msg inf">Descreva detalhadamente o problema com este pedido. '
               f'Nossa equipe irá analisar e responder em breve.</div>'
               f'<div class="card form-card">'
               f'<form method="post" action="/meu-pedido/reclamacao">'
               f'<input type="hidden" name="pedido_id" value="{pedido["id"]}">'
               f'<div class="field"><label>Descrição da reclamação *</label>'
               f'<textarea name="texto" required placeholder="Descreva o problema..."></textarea></div>'
               f'<div class="row-btn" style="margin-top:16px">'
               f'<button class="btn btn-primary" type="submit">📨 Enviar Reclamação</button>'
               f'<a class="btn btn-sec" href="/meus-pedidos">Cancelar</a>'
               f'</div></form></div>')
    return _layout("Reclamação",corpo,usuario)


def html_confirmar_cancelar_pedido(usuario, pedido):
    bloqueado=pedido["status_rastreio"] in RASTREIO_BLOQUEIO
    if bloqueado:
        corpo=(f"<h2>✕ Cancelar Pedido</h2>"
               f'<div class="msg err"><strong>Cancelamento não permitido.</strong><br>'
               f'O pedido <span class="mono">{_h(pedido["codigo_pedido"])}</span> '
               f'já foi <strong>{_h(pedido["status_rastreio"].lower())}</strong> e não pode mais ser cancelado.</div>'
               f'<a class="btn btn-sec" href="/meus-pedidos">← Voltar</a>')
        return _layout("Cancelar Pedido",corpo,usuario)
    aviso=(f'<div class="msg warn">💰 <strong>Pagamento já confirmado.</strong> '
           f'O valor de <strong>R$ {float(pedido["total"]):.2f}</strong> será estornado em até 5 dias úteis.</div>'
           if pedido["status_pagamento"]=="Pago"
           else '<div class="msg inf">Pagamento ainda não confirmado — nenhum valor será cobrado.</div>')
    try:    itens_d=json.loads(pedido.get("itens_json","[]"))
    except: itens_d=[]
    itens_html="".join(f"<li>{_h(i.get('nome',''))} × {i.get('qtd',1)}</li>" for i in itens_d)
    corpo=(f"<h2>✕ Cancelar Pedido</h2>{aviso}"
           f'<div class="card form-card" style="margin-bottom:16px">'
           f'<p><strong>Pedido:</strong> <span class="mono">{_h(pedido["codigo_pedido"])}</span></p>'
           f'<p style="margin:6px 0"><strong>Total:</strong> R$ {float(pedido["total"]):.2f}</p>'
           f'<p><strong>Data:</strong> {_h(pedido["data"])}</p>'
           f'<ul style="margin:10px 0 0 18px;font-size:.875rem">{itens_html}</ul></div>'
           f'<form method="post" action="/meu-pedido/excluir" style="display:flex;gap:10px">'
           f'<input type="hidden" name="id" value="{pedido["id"]}">'
           f'<button class="btn btn-danger" type="submit">Confirmar Cancelamento</button>'
           f'<a class="btn btn-sec" href="/meus-pedidos">Manter Pedido</a></form>')
    return _layout("Cancelar Pedido",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Mensagens (comprador)
# ═══════════════════════════════════════════════════════════════════════════════

def html_minhas_mensagens(usuario, erro="", msg=""):
    minhas=mensagens_do_usuario(usuario["id"])

    cards=""
    for m in minhas:
        cancelar=""
        if m["cancelada"]=="0":
            cancelar=(f'<form method="post" action="/mensagem/cancelar" style="display:inline">'
                      f'<input type="hidden" name="id" value="{m["id"]}">'
                      f'<button class="btn btn-danger btn-sm" type="submit">✕ Cancelar</button></form>')
        cards+=(f'<div class="msg-card">'
                f'<div class="mc-head">'
                f'{_badge_msg(m["lida"],m["cancelada"])}'
                f'<span style="font-size:.78rem;color:var(--muted);margin-left:auto">{_h(m["data"])}</span>'
                f'</div>'
                f'<div class="mc-body">{_h(m["texto"])}</div>'
                f'<div class="mc-foot">{cancelar}</div>'
                f'</div>')

    if not cards:
        cards='<p style="color:var(--muted);margin-bottom:20px">Nenhuma mensagem enviada ainda.</p>'

    corpo=(f"<h2>💬 Minhas Mensagens</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<h3>✉ Nova Mensagem para o Administrador</h3>'
           f'<div class="card form-card" style="margin-bottom:24px">'
           f'<form method="post" action="/mensagem/nova">'
           f'<div class="field"><label>Mensagem</label>'
           f'<textarea name="texto" required placeholder="Digite sua mensagem para a equipe da loja..."></textarea></div>'
           f'<button class="btn btn-primary" type="submit">📨 Enviar Mensagem</button>'
           f'</form></div>'
           f'<h3>Histórico de mensagens</h3>'
           f'{cards}')
    return _layout("Mensagens",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Admin: Painel
# ═══════════════════════════════════════════════════════════════════════════════

def html_admin_painel(usuario):
    np=len(produtos); nc=len([u for u in usuarios if u["tipo"]=="comprador"])
    na=len([u for u in usuarios if u["tipo"]=="admin"])
    ned=len(pedidos); npend=len([p for p in pedidos if p["status_pagamento"]=="Pendente"])
    nr=n_reclamacoes_abertas(); nm=n_mensagens_nao_lidas()
    corpo=(f"<h2>🏠 Painel Administrativo</h2>"
           f'<div class="stat-grid">'
           f'<div class="stat-card"><div class="stat-icon">📦</div><div class="stat-num">{np}</div><div class="stat-label">Produtos</div><a class="btn btn-primary btn-sm" href="/admin/produtos" style="margin-top:10px;display:inline-block">Gerenciar</a></div>'
           f'<div class="stat-card"><div class="stat-icon">👥</div><div class="stat-num">{nc}</div><div class="stat-label">Compradores</div><a class="btn btn-primary btn-sm" href="/admin/usuarios" style="margin-top:10px;display:inline-block">Gerenciar</a></div>'
           f'<div class="stat-card"><div class="stat-icon">🔑</div><div class="stat-num">{na}</div><div class="stat-label">Admins</div><a class="btn btn-primary btn-sm" href="/admin/usuarios" style="margin-top:10px;display:inline-block">Gerenciar</a></div>'
           f'<div class="stat-card"><div class="stat-icon">📋</div><div class="stat-num">{ned}</div><div class="stat-label">Pedidos</div><a class="btn btn-primary btn-sm" href="/admin/pedidos" style="margin-top:10px;display:inline-block">Ver todos</a></div>'
           f'<div class="stat-card"><div class="stat-icon">⏳</div><div class="stat-num">{npend}</div><div class="stat-label">Pend. Pagamento</div><a class="btn btn-warn btn-sm" href="/admin/pedidos" style="margin-top:10px;display:inline-block">Atender</a></div>'
           f'<div class="stat-card"><div class="stat-icon">⚠</div><div class="stat-num">{nr}</div><div class="stat-label">Reclamações Abertas</div><a class="btn btn-danger btn-sm" href="/admin/reclamacoes" style="margin-top:10px;display:inline-block">Ver</a></div>'
           f'<div class="stat-card"><div class="stat-icon">💬</div><div class="stat-num">{nm}</div><div class="stat-label">Msgs Não Lidas</div><a class="btn btn-sec btn-sm" href="/admin/mensagens" style="margin-top:10px;display:inline-block">Ver</a></div>'
           f'</div>')
    return _layout("Painel",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Admin: Usuários / Produtos
# ═══════════════════════════════════════════════════════════════════════════════

def html_admin_usuarios(usuario, campos={}, erro="", msg=""):
    v=lambda k:_h(campos.get(k,""))
    linhas="".join(
        f"<tr><td class='mono'>#{u['id']}</td><td>{_h(u['nome'])}</td>"
        f"<td>{_h(u['email'])}</td><td>{_h(u['telefone'])}</td>"
        f"<td>{_badge_tipo(u['tipo'])}</td>"
        f"<td><a class='btn btn-sec btn-sm' href='/admin/usuario/editar?id={u['id']}'>✏ Editar</a></td></tr>"
        for u in usuarios) or '<tr><td colspan="6" class="vazio">Nenhum usuário.</td></tr>'
    corpo=(f"<h2>👥 Usuários</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<div class="tbl-wrap"><table>'
           f'<thead><tr><th>#</th><th>Nome</th><th>E-mail</th><th>Telefone</th><th>Tipo</th><th>Ação</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>'
           f'<h3>➕ Novo Administrador</h3>'
           f'<div class="card form-card"><form method="post" action="/admin/usuarios/novo">'
           f'<div class="field"><label>Nome</label><input name="nome" value="{v("nome")}" required></div>'
           f'<div class="field"><label>E-mail</label><input type="email" name="email" value="{v("email")}" required></div>'
           f'<div class="field"><label>Telefone</label><input name="telefone" value="{v("telefone")}" required placeholder="(11) 99999-9999"></div>'
           f'<div class="field"><label>Senha</label><input type="password" name="senha" required></div>'
           f'<div class="field"><label>Confirmar senha</label><input type="password" name="senha2" required></div>'
           f'<button class="btn btn-primary" type="submit">Cadastrar Admin</button>'
           f'</form></div>')
    return _layout("Usuários",corpo,usuario)

def html_admin_produtos(usuario, msg="", erro=""):
    linhas=""
    for p in produtos:
        th=(f'<img src="/fotos/{_h(p["codigo"])}.png" style="width:52px;height:39px;object-fit:cover;border-radius:4px">'
            if foto_existe(p["codigo"]) else '<span style="font-size:1.3rem">📷</span>')
        linhas+=(f"<tr><td>{th}</td><td class='mono'>{_h(p['codigo'])}</td>"
                 f"<td>{_h(p['nome'])}</td><td>R$ {float(p['preco']):.2f}</td>"
                 f"<td>{_h(p['prazo_entrega'])}</td>"
                 f"<td style='white-space:nowrap'>"
                 f"<a class='btn btn-sec btn-sm' href='/admin/produto/editar?id={p['id']}'>✏ Editar</a> "
                 f"<a class='btn btn-danger btn-sm' href='/admin/produto/excluir?id={p['id']}'>🗑 Excluir</a>"
                 f"</td></tr>")
    if not linhas: linhas='<tr><td colspan="6" class="vazio">Nenhum produto.</td></tr>'
    corpo=(f"<h2>📦 Produtos</h2>{_msg(msg,'ok')}{_msg(erro,'err')}"
           f'<a class="btn btn-primary" href="/admin/produto/novo" style="margin-bottom:18px;display:inline-block">+ Novo Produto</a>'
           f'<div class="tbl-wrap"><table>'
           f'<thead><tr><th>Foto</th><th>Código</th><th>Nome</th><th>Preço</th><th>Prazo</th><th>Ações</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>')
    return _layout("Produtos",corpo,usuario)

def html_admin_form_produto(usuario, produto=None, erro=""):
    acao  ="/admin/produto/editar" if produto else "/admin/produto/novo"
    titulo="✏ Editar Produto"     if produto else "➕ Novo Produto"
    v=lambda k,d="":_h(str(produto[k]) if produto and produto.get(k) is not None else d)
    id_hid=f'<input type="hidden" name="id" value="{v("id")}">' if produto else ""
    if produto:
        cod=produto.get("codigo","")
        foto_atual=(f'<div style="margin-bottom:8px"><p style="font-size:.8rem;color:var(--muted);margin-bottom:6px">Foto atual:</p>'
                    f'{_img_prod(cod)}</div>') if foto_existe(cod) else '<div class="sem-foto">📷 Sem foto</div>'
        nota="Opcional — em branco mantém a foto atual."
        cod_info=f'<p class="msg inf">Código: <strong>{_h(produto["codigo"])}</strong></p>'
    else:
        foto_atual=""; nota="Opcional — pode ser adicionada depois."
        cod_info='<p class="msg inf">O <strong>código</strong> será gerado automaticamente.</p>'
    # Extrai número de dias para pré-preencher o campo numérico
    dias=v("prazo_entrega","5") if not produto else extrair_dias(produto.get("prazo_entrega","5"))
    corpo=(f"<h2>{titulo}</h2>{_msg(erro,'err')}{cod_info}"
           f'<div class="card form-card">'
           f'<form method="post" action="{acao}" enctype="multipart/form-data">'
           f'{id_hid}'
           f'<div class="field"><label>Nome *</label><input name="nome" value="{v("nome")}" required></div>'
           f'<div class="field"><label>Descrição</label><textarea name="descricao">{v("descricao")}</textarea></div>'
           f'<div class="field"><label>Foto (JPEG / PNG / GIF / WEBP / BMP)</label>'
           f'{foto_atual}<input type="file" name="foto" accept="image/*">'
           f'<small>Redimensionada para {FOTO_W}×{FOTO_H} px e salva como PNG. {nota}</small></div>'
           f'<div class="field"><label>Preço (R$) *</label>'
           f'<input type="number" step="0.01" min="0" name="preco" value="{v("preco","0.00")}" required></div>'
           f'<div class="field"><label>Prazo de entrega (dias úteis) *</label>'
           f'<div class="dias-field">'
           f'<input type="number" min="1" max="999" name="prazo_dias" value="{dias}" required>'
           f'<span>dias úteis</span></div>'
           f'<small>O prazo será sempre exibido em dias úteis.</small></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">{"💾 Salvar" if produto else "📦 Cadastrar"}</button>'
           f'<a class="btn btn-sec" href="/admin/produtos">Cancelar</a>'
           f'</div></form></div>')
    return _layout(titulo,corpo,usuario)

def html_admin_confirmar_excluir(usuario, produto):
    corpo=(f"<h2>🗑 Confirmar Exclusão</h2>"
           f'<p style="margin-bottom:16px;color:var(--muted)">Esta ação não pode ser desfeita. A foto também será removida.</p>'
           f'<div class="card form-card" style="margin-bottom:16px">'
           f'<p><strong>Código:</strong> <span class="mono">{_h(produto["codigo"])}</span></p>'
           f'<p style="margin:8px 0"><strong>Nome:</strong> {_h(produto["nome"])}</p>'
           f'<p><strong>Preço:</strong> R$ {float(produto["preco"]):.2f}</p>'
           f'<div style="margin-top:12px">{_img_prod(produto["codigo"])}</div></div>'
           f'<form method="post" action="/admin/produto/excluir" style="display:flex;gap:10px">'
           f'<input type="hidden" name="id" value="{produto["id"]}">'
           f'<button class="btn btn-danger" type="submit">Confirmar</button>'
           f'<a class="btn btn-sec" href="/admin/produtos">Cancelar</a></form>')
    return _layout("Excluir",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  VIEW — Admin: Pedidos / Reclamações / Mensagens
# ═══════════════════════════════════════════════════════════════════════════════

def html_admin_pedidos(usuario, msg=""):
    todos=sorted(pedidos,key=lambda x:x["id"],reverse=True); linhas=""
    for ped in todos:
        u2=buscar_id(usuarios,ped["usuario_id"])
        linhas+=(f"<tr><td class='mono' style='white-space:nowrap'>{_h(ped['codigo_pedido'])}</td>"
                 f"<td>{_h(u2['nome'] if u2 else '—')}</td>"
                 f"<td>R$ {float(ped['total']):.2f}</td>"
                 f"<td>{_badge_pag(ped['status_pagamento'])}</td>"
                 f"<td>{_badge_rat(ped['status_rastreio'])}</td>"
                 f"<td style='font-size:.78rem;color:var(--muted)'>{_h(ped['data'])}</td>"
                 f"<td><a class='btn btn-sec btn-sm' href='/admin/pedido/status?id={ped['id']}'>✏ Status</a></td></tr>")
    if not linhas: linhas='<tr><td colspan="7" class="vazio">Nenhum pedido.</td></tr>'
    corpo=(f"<h2>📋 Pedidos</h2>{_msg(msg,'ok')}"
           f'<div class="tbl-wrap"><table>'
           f'<thead><tr><th>Código</th><th>Cliente</th><th>Total</th>'
           f'<th>Pagamento</th><th>Rastreio</th><th>Data</th><th>Ação</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>')
    return _layout("Pedidos",corpo,usuario)

def html_admin_status_pedido(usuario, pedido, erro=""):
    u2=buscar_id(usuarios,pedido["usuario_id"])
    opts_pag="".join(f'<option value="{s}" {"selected" if pedido["status_pagamento"]==s else ""}>{s}</option>'
                     for s in STATUS_PAGAMENTO)
    opts_rat="".join(f'<option value="{s}" {"selected" if pedido["status_rastreio"]==s else ""}>{s}</option>'
                     for s in STATUS_RASTREIO)
    corpo=(f"<h2>✏ Status do Pedido</h2>{_msg(erro,'err')}"
           f'<div class="card form-card">'
           f'<p><strong>Pedido:</strong> <span class="mono">{_h(pedido["codigo_pedido"])}</span></p>'
           f'<p style="margin:6px 0"><strong>Cliente:</strong> {_h(u2["nome"] if u2 else "—")}</p>'
           f'<p><strong>Total:</strong> R$ {float(pedido["total"]):.2f}</p>'
           f'<hr class="sep-form">'
           f'<form method="post" action="/admin/pedido/status">'
           f'<input type="hidden" name="id" value="{pedido["id"]}">'
           f'<div class="field"><label>Pagamento</label><select name="status_pagamento">{opts_pag}</select></div>'
           f'<div class="field"><label>Rastreio / Entrega</label><select name="status_rastreio">{opts_rat}</select></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">💾 Salvar</button>'
           f'<a class="btn btn-sec" href="/admin/pedidos">Cancelar</a>'
           f'</div></form></div>')
    return _layout("Status",corpo,usuario)

def html_admin_reclamacoes(usuario, msg=""):
    """Lista todas as reclamações com link para responder."""
    todas=sorted(reclamacoes,key=lambda x:x["id"],reverse=True); linhas=""
    for r in todas:
        u2=buscar_id(usuarios,r["usuario_id"])
        ped=buscar_id(pedidos,r["pedido_id"])
        cod_ped=ped["codigo_pedido"] if ped else f"#{r['pedido_id']}"
        nome_u  = u2['nome']  if u2 else '—'
        email_u = u2['email'] if u2 else '—'
        linhas+=(f"<tr><td class='mono' style='white-space:nowrap'>{_h(cod_ped)}</td>"
                 f"<td><div>{_h(nome_u)}</div><div style='font-size:.75rem;color:var(--muted)'>{_h(email_u)}</div></td>"
                 f"<td style='max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{_h(r['texto'][:80])}</td>"
                 f"<td>{_badge_recl(r['status'])}</td>"
                 f"<td style='font-size:.78rem;color:var(--muted)'>{_h(r['data'])}</td>"
                 f"<td><a class='btn btn-sec btn-sm' href='/admin/reclamacao/responder?id={r['id']}'>✏ Responder</a></td></tr>")
    if not linhas: linhas='<tr><td colspan="6" class="vazio">Nenhuma reclamação.</td></tr>'
    corpo=(f"<h2>⚠ Reclamações</h2>{_msg(msg,'ok')}"
           f'<div class="tbl-wrap"><table>'
           f'<thead><tr><th>Pedido</th><th>Cliente</th><th>Reclamação</th><th>Status</th><th>Data</th><th>Ação</th></tr></thead>'
           f'<tbody>{linhas}</tbody></table></div>')
    return _layout("Reclamações",corpo,usuario)

def html_admin_reclamacao_responder(usuario, recl, erro=""):
    u2=buscar_id(usuarios,recl["usuario_id"])
    ped=buscar_id(pedidos,recl["pedido_id"])
    cod_ped=ped["codigo_pedido"] if ped else f"#{recl['pedido_id']}"
    opts="".join(f'<option value="{s}" {"selected" if recl["status"]==s else ""}>{s}</option>'
                 for s in STATUS_RECLAMACAO)
    corpo=(f"<h2>✏ Responder Reclamação</h2>{_msg(erro,'err')}"
           f'<div class="reclam-box" style="margin-bottom:18px">'
           f'<div class="rb-head">Pedido <span class="mono">{_h(cod_ped)}</span> — '
           f'{_h(u2["nome"] if u2 else "—")} '
           f'<span style="font-size:.8rem;color:var(--muted);font-weight:400">({_h(u2["email"] if u2 else "")})</span> '
           f'— {_h(recl["data"])}</div>'
           f'<div class="rb-body">{_h(recl["texto"])}</div></div>'
           f'<div class="card form-card">'
           f'<form method="post" action="/admin/reclamacao/responder">'
           f'<input type="hidden" name="id" value="{recl["id"]}">'
           f'<div class="field"><label>Status</label><select name="status">{opts}</select></div>'
           f'<div class="field"><label>Resposta ao cliente (opcional)</label>'
           f'<textarea name="resposta">{_h(recl.get("resposta",""))}</textarea></div>'
           f'<div class="row-btn" style="margin-top:16px">'
           f'<button class="btn btn-primary" type="submit">💾 Salvar Resposta</button>'
           f'<a class="btn btn-sec" href="/admin/reclamacoes">Cancelar</a>'
           f'</div></form></div>')
    return _layout("Reclamação",corpo,usuario)

def html_admin_mensagens(usuario):
    """Lista todas as mensagens não canceladas; marca as não lidas como lidas."""
    # Marca como lidas ao visualizar
    alterado=False
    for m in mensagens:
        if m["cancelada"]=="0" and m["lida"]=="0":
            m["lida"]="1"; alterado=True
    if alterado: salv_mensagens()

    todas=sorted([m for m in mensagens if m["cancelada"]=="0"],
                 key=lambda x:x["id"],reverse=True)
    cards=""
    for m in todas:
        u2=buscar_id(usuarios,m["usuario_id"])
        nome_u  = u2['nome']  if u2 else '—'
        email_u = u2['email'] if u2 else '—'
        cards+=(f'<div class="msg-card">'
                f'<div class="mc-head">'
                f'<strong>{_h(nome_u)}</strong> '
                f'<span style="font-size:.78rem;color:var(--muted)">({_h(email_u)})</span>'
                f'<span style="font-size:.78rem;color:var(--muted);margin-left:auto">{_h(m["data"])}</span>'
                f'</div>'
                f'<div class="mc-body">{_h(m["texto"])}</div>'
                f'</div>')
    if not cards: cards='<p style="color:var(--muted)">Nenhuma mensagem.</p>'
    corpo=(f"<h2>💬 Mensagens dos Compradores</h2>{cards}")
    return _layout("Mensagens",corpo,usuario)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTROLLER
# ═══════════════════════════════════════════════════════════════════════════════

class LojaHandler(BaseHTTPRequestHandler):

    def log_message(self,fmt,*args): pass

    def _cookie(self):
        raw=self.headers.get("Cookie","")
        for p in raw.split(";"):
            p=p.strip()
            if p.startswith("sessao="): return p[7:]
        return None

    def _u(self): return usuario_logado(self._cookie())

    def _resp(self,html,status=200,he=None):
        body=html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        if he:
            for k,v in he.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)

    def _resp_bin(self,dados,ct,status=200):
        self.send_response(status)
        self.send_header("Content-Type",ct)
        self.send_header("Content-Length",str(len(dados)))
        self.send_header("Cache-Control","max-age=3600")
        self.end_headers(); self.wfile.write(dados)

    def _redir(self,url,he=None):
        self.send_response(302); self.send_header("Location",url)
        if he:
            for k,v in he.items(): self.send_header(k,v)
        self.end_headers()

    def _post(self):
        n=int(self.headers.get("Content-Length",0)); raw=self.rfile.read(n)
        ct=self.headers.get("Content-Type","")
        if "multipart/form-data" in ct:
            m=re.search(r'boundary=([^\s;]+)',ct)
            if not m: return {}
            return _parse_multipart(raw,m.group(1).strip('"'))
        return {k:unquote_plus(v[0])
                for k,v in parse_qs(raw.decode("utf-8",errors="replace"),
                                    keep_blank_values=True).items()}

    def _login_req(self,tipo=None):
        u=self._u()
        if not u: self._redir("/login"); return None
        if tipo and u["tipo"]!=tipo:
            self._redir("/admin" if u["tipo"]=="admin" else "/catalogo"); return None
        return u

    # ── Edição de usuário (reutilizado por perfil e admin) ────────────────────

    def _editar_usuario(self,alvo,campos,pode_tipo=False):
        nome =campos.get("nome","").strip()
        email=campos.get("email","").strip()
        email2=campos.get("email2","").strip()
        tel  =campos.get("telefone","").strip()
        senha=campos.get("senha","").strip()
        senha2=campos.get("senha2","").strip()
        tipo =campos.get("tipo","").strip() if pode_tipo else alvo["tipo"]
        if not all([nome,email,email2,tel]):
            return "⚠ Nome, e-mail e telefone são obrigatórios."
        if email!=email2: return "⚠ Os e-mails não coincidem."
        if not ok_email(email): return "⚠ E-mail inválido."
        if not ok_tel(tel): return "⚠ Telefone inválido."
        if any(u["email"].lower()==email.lower() and u["id"]!=alvo["id"] for u in usuarios):
            return "⚠ E-mail já cadastrado por outro usuário."
        if pode_tipo and tipo not in ["admin","comprador"]:
            return "⚠ Tipo inválido."
        if senha or senha2:
            if len(senha)<6: return "⚠ Senha deve ter pelo menos 6 caracteres."
            if senha!=senha2: return "⚠ As senhas não coincidem."
            alvo["senha_hash"]=hash_senha(senha)
        alvo["nome"]=nome; alvo["email"]=email; alvo["telefone"]=tel
        if pode_tipo: alvo["tipo"]=tipo
        salv_usuarios(); return None

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed=urlparse(self.path); path=parsed.path
        qs=parse_qs(parsed.query); _q=lambda k,d="":unquote_plus(qs.get(k,[d])[0])
        u=self._u()

        if path in ("/",""): self._redir("/catalogo")

        elif path.startswith("/fotos/"):
            arq=os.path.basename(path)
            if not arq.endswith(".png"): self._resp("",404); return
            fp=os.path.join(PASTA_FOTOS,arq)
            if os.path.exists(fp):
                with open(fp,"rb") as f: self._resp_bin(f.read(),"image/png")
            else: self._resp("",404)

        elif path=="/login":
            if u: self._redir("/admin" if u["tipo"]=="admin" else "/catalogo"); return
            self._resp(html_login())
        elif path=="/registrar":
            if u: self._redir("/catalogo"); return
            self._resp(html_registrar())
        elif path=="/logout":
            tok=self._cookie()
            if tok: destruir_sessao(tok)
            self._redir("/login",{"Set-Cookie":"sessao=; Max-Age=0; Path=/"})

        elif path=="/catalogo":
            busca=_q("q"); lista=produtos
            if busca:
                t=busca.lower()
                lista=[p for p in produtos if t in p["nome"].lower() or t in p["descricao"].lower()]
            self._resp(html_catalogo(u,lista,busca,_q("msg")))
        elif path=="/carrinho":
            if not (u:=self._login_req("comprador")): return
            self._resp(html_carrinho(u,_q("msg"),_q("erro")))
        elif path=="/pix":
            if not (u:=self._login_req("comprador")): return
            try:
                ped=buscar_id(pedidos,int(_q("id")))
                if ped and ped["usuario_id"]==u["id"]: self._resp(html_pix(u,ped))
                else: self._redir("/meus-pedidos")
            except: self._redir("/meus-pedidos")
        elif path=="/meus-pedidos":
            if not (u:=self._login_req("comprador")): return
            self._resp(html_meus_pedidos(u,_q("msg"),_q("erro")))
        elif path=="/meu-pedido/excluir":
            if not (u:=self._login_req("comprador")): return
            try:
                ped=buscar_id(pedidos,int(_q("id")))
                if ped and ped["usuario_id"]==u["id"]: self._resp(html_confirmar_cancelar_pedido(u,ped))
                else: self._redir("/meus-pedidos")
            except: self._redir("/meus-pedidos")

        # ── Reclamação ─────────────────────────────────────────────────────
        elif path=="/meu-pedido/reclamacao":
            if not (u:=self._login_req("comprador")): return
            try:
                ped=buscar_id(pedidos,int(_q("id")))
                if not ped or ped["usuario_id"]!=u["id"]:
                    self._redir("/meus-pedidos"); return
                recl=reclamacao_do_pedido(ped["id"])
                self._resp(html_reclamacao(u,ped,recl,msg=_q("msg")))
            except: self._redir("/meus-pedidos")

        # ── Mensagens (comprador) ───────────────────────────────────────────
        elif path=="/minhas-mensagens":
            if not (u:=self._login_req("comprador")): return
            self._resp(html_minhas_mensagens(u,msg=_q("msg"),erro=_q("erro")))

        # ── Perfil ─────────────────────────────────────────────────────────
        elif path=="/meu-perfil":
            if not (u:=self._login_req()): return
            self._resp(html_editar_perfil(u,u,msg=_q("msg")))

        # ── Admin ──────────────────────────────────────────────────────────
        elif path=="/admin":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_painel(u))
        elif path=="/admin/usuarios":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_usuarios(u,msg=_q("msg"),erro=_q("erro")))
        elif path=="/admin/usuario/editar":
            if not (u:=self._login_req("admin")): return
            try:
                alvo=buscar_id(usuarios,int(_q("id")))
                if alvo: self._resp(html_editar_perfil(u,alvo,admin_editando=True))
                else: self._redir(f"/admin/usuarios?erro={quote_plus('Usuário não encontrado.')}")
            except: self._redir("/admin/usuarios")
        elif path=="/admin/produtos":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_produtos(u,_q("msg"),_q("erro")))
        elif path=="/admin/produto/novo":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_form_produto(u))
        elif path=="/admin/produto/editar":
            if not (u:=self._login_req("admin")): return
            try:
                p=buscar_id(produtos,int(_q("id")))
                if p: self._resp(html_admin_form_produto(u,p))
                else: self._redir(f"/admin/produtos?erro={quote_plus('Produto não encontrado.')}")
            except: self._redir("/admin/produtos")
        elif path=="/admin/produto/excluir":
            if not (u:=self._login_req("admin")): return
            try:
                p=buscar_id(produtos,int(_q("id")))
                if p: self._resp(html_admin_confirmar_excluir(u,p))
                else: self._redir(f"/admin/produtos?erro={quote_plus('Produto não encontrado.')}")
            except: self._redir("/admin/produtos")
        elif path=="/admin/pedidos":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_pedidos(u,_q("msg")))
        elif path=="/admin/pedido/status":
            if not (u:=self._login_req("admin")): return
            try:
                ped=buscar_id(pedidos,int(_q("id")))
                if ped: self._resp(html_admin_status_pedido(u,ped))
                else: self._redir("/admin/pedidos")
            except: self._redir("/admin/pedidos")
        elif path=="/admin/reclamacoes":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_reclamacoes(u,_q("msg")))
        elif path=="/admin/reclamacao/responder":
            if not (u:=self._login_req("admin")): return
            try:
                recl=buscar_id(reclamacoes,int(_q("id")))
                if recl: self._resp(html_admin_reclamacao_responder(u,recl))
                else: self._redir("/admin/reclamacoes")
            except: self._redir("/admin/reclamacoes")
        elif path=="/admin/mensagens":
            if not (u:=self._login_req("admin")): return
            self._resp(html_admin_mensagens(u))
        else:
            self._resp("<h1 style='font-family:sans-serif;padding:40px'>404</h1>",404)

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        path=urlparse(self.path).path; campos=self._post(); u=self._u()
        def _f(k): v=campos.get(k,""); return (v if isinstance(v,str) else "").strip()
        def _arq(k):
            v=campos.get(k)
            if isinstance(v,tuple) and len(v)==2:
                n,d=v
                if n and d: return n,d
            return None

        # ── Auth ───────────────────────────────────────────────────────────
        if path=="/login":
            usr=next((x for x in usuarios
                      if x["email"].lower()==_f("email").lower()
                      and x["senha_hash"]==hash_senha(_f("senha"))),None)
            if not usr: self._resp(html_login("⚠ E-mail ou senha incorretos.")); return
            tok=criar_sessao(usr["id"])
            self._redir("/admin" if usr["tipo"]=="admin" else "/catalogo",
                        {"Set-Cookie":f"sessao={tok}; Path=/; HttpOnly"})

        elif path=="/registrar":
            nome=_f("nome"); email=_f("email"); email2=_f("email2")
            tel=_f("telefone"); senha=_f("senha"); senha2=_f("senha2")
            c={"nome":nome,"email":email,"email2":email2,"telefone":tel}
            if not all([nome,email,email2,tel,senha,senha2]):
                self._resp(html_registrar(c,"⚠ Todos os campos são obrigatórios.")); return
            if email!=email2: self._resp(html_registrar(c,"⚠ Os e-mails não coincidem.")); return
            if not ok_email(email): self._resp(html_registrar(c,"⚠ E-mail inválido.")); return
            if not ok_tel(tel): self._resp(html_registrar(c,"⚠ Telefone inválido.")); return
            if len(senha)<6: self._resp(html_registrar(c,"⚠ Senha deve ter ≥ 6 caracteres.")); return
            if senha!=senha2: self._resp(html_registrar(c,"⚠ As senhas não coincidem.")); return
            if any(x["email"].lower()==email.lower() for x in usuarios):
                self._resp(html_registrar(c,"⚠ E-mail já cadastrado.")); return
            novo={"id":prox_id(usuarios),"nome":nome,"email":email,
                  "telefone":tel,"senha_hash":hash_senha(senha),"tipo":"comprador"}
            usuarios.append(novo); salv_usuarios()
            tok=criar_sessao(novo["id"])
            self._redir(f"/catalogo?msg={quote_plus('Bem-vindo(a), '+nome+'! 🎉')}",
                        {"Set-Cookie":f"sessao={tok}; Path=/; HttpOnly"})

        # ── Perfil ─────────────────────────────────────────────────────────
        elif path=="/meu-perfil":
            if not (u:=self._login_req()): return
            erro=self._editar_usuario(u,campos)
            if erro: self._resp(html_editar_perfil(u,u,campos,erro)); return
            self._redir(f"/meu-perfil?msg={quote_plus('Dados atualizados! ✅')}")

        elif path=="/admin/usuario/editar":
            if not (u:=self._login_req("admin")): return
            qs2=parse_qs(urlparse(self.path).query)
            try: uid=int(qs2.get("id",["0"])[0])
            except: self._redir("/admin/usuarios"); return
            alvo=buscar_id(usuarios,uid)
            if not alvo:
                self._redir(f"/admin/usuarios?erro={quote_plus('Usuário não encontrado.')}"); return
            erro=self._editar_usuario(alvo,campos,pode_tipo=True)
            if erro: self._resp(html_editar_perfil(u,alvo,campos,erro,admin_editando=True)); return
            self._redir(f"/admin/usuarios?msg={quote_plus(alvo['nome']+' atualizado! ✅')}")

        # ── Carrinho ───────────────────────────────────────────────────────
        elif path=="/carrinho/adicionar":
            if not u or u["tipo"]!="comprador": self._redir("/login"); return
            try:
                pid=int(_f("produto_id")); p=buscar_id(produtos,pid)
                if not p: self._redir("/catalogo"); return
                ex=next((ci for ci in carrinho
                         if ci["usuario_id"]==u["id"] and ci["produto_id"]==pid),None)
                if ex: ex["quantidade"]+=1
                else: carrinho.append({"id":prox_id(carrinho),"usuario_id":u["id"],
                                       "produto_id":pid,"quantidade":1})
                salv_carrinho()
                self._redir(f"/catalogo?msg={quote_plus(p['nome']+' adicionado! 🛒')}")
            except: self._redir("/catalogo")

        elif path=="/carrinho/remover":
            if not u or u["tipo"]!="comprador": self._redir("/login"); return
            try:
                iid=int(_f("item_id")); item=buscar_id(carrinho,iid)
                if item and item["usuario_id"]==u["id"]:
                    carrinho.remove(item); salv_carrinho()
                self._redir(f"/carrinho?msg={quote_plus('Item removido.')}")
            except: self._redir("/carrinho")

        elif path=="/carrinho/finalizar":
            if not u or u["tipo"]!="comprador": self._redir("/login"); return
            itens=itens_do_carrinho(u["id"])
            if not itens:
                self._redir(f"/carrinho?erro={quote_plus('Carrinho vazio!')}"); return
            total=0.0; snap=[]
            for item in itens:
                p=buscar_id(produtos,item["produto_id"])
                if not p: continue
                sub=float(p["preco"])*item["quantidade"]; total+=sub
                snap.append({"nome":p["nome"],"codigo":p["codigo"],
                             "qtd":item["quantidade"],"preco":float(p["preco"]),"subtotal":sub})
            cods={p["codigo_pedido"] for p in pedidos}
            cod=gerar_codigo_pedido()
            while cod in cods: cod=gerar_codigo_pedido()
            novo={"id":prox_id(pedidos),"codigo_pedido":cod,
                  "codigo_rastreio":gerar_codigo_rastreio(),"usuario_id":u["id"],
                  "itens_json":json.dumps(snap,ensure_ascii=False),
                  "total":round(total,2),"status_pagamento":"Pendente",
                  "status_rastreio":"Separando o pedido","data":agora()}
            pedidos.append(novo); salv_pedidos()
            for item in itens: carrinho.remove(item)
            salv_carrinho()
            self._redir(f"/pix?id={novo['id']}")

        # ── Cancelar pedido ────────────────────────────────────────────────
        elif path=="/meu-pedido/excluir":
            if not (u:=self._login_req("comprador")): return
            try: pid=int(_f("id"))
            except: self._redir("/meus-pedidos"); return
            ped=buscar_id(pedidos,pid)
            if not ped or ped["usuario_id"]!=u["id"]:
                self._redir("/meus-pedidos"); return
            if ped["status_rastreio"] in RASTREIO_BLOQUEIO:
                self._redir(f"/meus-pedidos?erro={quote_plus('Pedido já enviado — não é possível cancelar.')}"); return
            # Remove reclamação vinculada
            recl=reclamacao_do_pedido(ped["id"])
            if recl: reclamacoes.remove(recl); salv_reclamacoes()
            pedidos.remove(ped); salv_pedidos()
            msg=(f"Pedido {ped['codigo_pedido']} cancelado. Estorno de R$ {float(ped['total']):.2f} em até 5 dias úteis."
                 if ped["status_pagamento"]=="Pago"
                 else f"Pedido {ped['codigo_pedido']} cancelado. Nenhum valor cobrado.")
            self._redir(f"/meus-pedidos?msg={quote_plus(msg)}")

        # ── Reclamação (comprador) ─────────────────────────────────────────
        elif path=="/meu-pedido/reclamacao":
            if not (u:=self._login_req("comprador")): return
            try: pedido_id=int(_f("pedido_id"))
            except: self._redir("/meus-pedidos"); return
            ped=buscar_id(pedidos,pedido_id)
            if not ped or ped["usuario_id"]!=u["id"]:
                self._redir("/meus-pedidos"); return
            if reclamacao_do_pedido(pedido_id):
                self._redir(f"/meu-pedido/reclamacao?id={pedido_id}"); return
            texto=_f("texto")
            if not texto.strip():
                self._resp(html_reclamacao(u,ped,None,"⚠ A descrição da reclamação é obrigatória.")); return
            nova={"id":prox_id(reclamacoes),"pedido_id":pedido_id,
                  "usuario_id":u["id"],"texto":texto,"status":"Aberta",
                  "resposta":"","data":agora()}
            reclamacoes.append(nova); salv_reclamacoes()
            self._redir(f"/meu-pedido/reclamacao?id={pedido_id}&msg={quote_plus('Reclamação enviada! ✅')}")

        # ── Mensagens (comprador) ──────────────────────────────────────────
        elif path=="/mensagem/nova":
            if not (u:=self._login_req("comprador")): return
            texto=_f("texto")
            if not texto.strip():
                self._resp(html_minhas_mensagens(u,erro="⚠ A mensagem não pode estar vazia.")); return
            nova={"id":prox_id(mensagens),"usuario_id":u["id"],
                  "texto":texto,"lida":"0","cancelada":"0","data":agora()}
            mensagens.append(nova); salv_mensagens()
            self._redir(f"/minhas-mensagens?msg={quote_plus('Mensagem enviada! ✅')}")

        elif path=="/mensagem/cancelar":
            if not (u:=self._login_req("comprador")): return
            try: mid=int(_f("id"))
            except: self._redir("/minhas-mensagens"); return
            msg_obj=buscar_id(mensagens,mid)
            if msg_obj and msg_obj["usuario_id"]==u["id"]:
                msg_obj["cancelada"]="1"; salv_mensagens()
            self._redir(f"/minhas-mensagens?msg={quote_plus('Mensagem cancelada.')}")

        # ── Admin: Novo admin ──────────────────────────────────────────────
        elif path=="/admin/usuarios/novo":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            nome=_f("nome"); email=_f("email"); tel=_f("telefone")
            senha=_f("senha"); senha2=_f("senha2")
            c={"nome":nome,"email":email,"telefone":tel}
            if not all([nome,email,tel,senha,senha2]):
                self._resp(html_admin_usuarios(u,c,"⚠ Todos os campos obrigatórios.")); return
            if not ok_email(email): self._resp(html_admin_usuarios(u,c,"⚠ E-mail inválido.")); return
            if len(senha)<6: self._resp(html_admin_usuarios(u,c,"⚠ Senha muito curta.")); return
            if senha!=senha2: self._resp(html_admin_usuarios(u,c,"⚠ Senhas não coincidem.")); return
            if any(x["email"].lower()==email.lower() for x in usuarios):
                self._resp(html_admin_usuarios(u,c,"⚠ E-mail já cadastrado.")); return
            novo={"id":prox_id(usuarios),"nome":nome,"email":email,
                  "telefone":tel,"senha_hash":hash_senha(senha),"tipo":"admin"}
            usuarios.append(novo); salv_usuarios()
            self._redir(f"/admin/usuarios?msg={quote_plus('Admin '+nome+' cadastrado! ✅')}")

        # ── Admin: CRUD produtos ───────────────────────────────────────────
        elif path=="/admin/produto/novo":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            nome=_f("nome"); desc=_f("descricao"); arq=_arq("foto")
            try:    preco=float(_f("preco").replace(",","."))
            except: preco=-1.0
            try:    dias=int(_f("prazo_dias")); assert dias>=1
            except: dias=0
            if not nome or preco<0 or dias<1:
                self._resp(html_admin_form_produto(u,erro="⚠ Nome, preço (≥ 0) e prazo (≥ 1 dia) são obrigatórios.")); return
            prazo=f"{dias} dias úteis"
            cods={p["codigo"] for p in produtos}
            cod=gerar_codigo_produto()
            while cod in cods: cod=gerar_codigo_produto()
            if arq:
                _,dados=arq; png=processar_foto(dados)
                if png is None:
                    self._resp(html_admin_form_produto(u,erro=f"⚠ Imagem inválida. Aceitos: {', '.join(sorted(FORMATOS_ACEITOS))}.")); return
                salvar_foto(cod,png)
            novo={"id":prox_id(produtos),"codigo":cod,"nome":nome,
                  "descricao":desc,"preco":round(preco,2),"prazo_entrega":prazo}
            produtos.append(novo); salv_produtos()
            self._redir(f"/admin/produtos?msg={quote_plus('«'+nome+'» cadastrado! Código: '+cod+' ✅')}")

        elif path=="/admin/produto/editar":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            try:    pid=int(_f("id"))
            except: self._redir("/admin/produtos"); return
            p=buscar_id(produtos,pid)
            if not p:
                self._redir(f"/admin/produtos?erro={quote_plus('Produto não encontrado.')}"); return
            nome=_f("nome"); desc=_f("descricao"); arq=_arq("foto")
            try:    preco=float(_f("preco").replace(",","."))
            except: preco=-1.0
            try:    dias=int(_f("prazo_dias")); assert dias>=1
            except: dias=0
            if not nome or preco<0 or dias<1:
                self._resp(html_admin_form_produto(u,p,"⚠ Nome, preço e prazo obrigatórios.")); return
            prazo=f"{dias} dias úteis"
            if arq:
                _,dados=arq; png=processar_foto(dados)
                if png is None:
                    self._resp(html_admin_form_produto(u,p,f"⚠ Imagem inválida. Aceitos: {', '.join(sorted(FORMATOS_ACEITOS))}.")); return
                salvar_foto(p["codigo"],png)
            p["nome"]=nome; p["descricao"]=desc; p["preco"]=round(preco,2); p["prazo_entrega"]=prazo
            salv_produtos()
            self._redir(f"/admin/produtos?msg={quote_plus('«'+nome+'» atualizado! ✅')}")

        elif path=="/admin/produto/excluir":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            try:    pid=int(_f("id"))
            except: self._redir("/admin/produtos"); return
            p=buscar_id(produtos,pid)
            if p:
                fp=caminho_foto(p["codigo"])
                if os.path.exists(fp): os.remove(fp)
                produtos.remove(p); salv_produtos()
                self._redir(f"/admin/produtos?msg={quote_plus('«'+p['nome']+'» excluído. ✅')}")
            else:
                self._redir(f"/admin/produtos?erro={quote_plus('Produto não encontrado.')}")

        elif path=="/admin/pedido/status":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            try:    pid=int(_f("id"))
            except: self._redir("/admin/pedidos"); return
            ped=buscar_id(pedidos,pid)
            if not ped: self._redir("/admin/pedidos"); return
            sp=_f("status_pagamento"); sr=_f("status_rastreio")
            if sp not in STATUS_PAGAMENTO:
                self._resp(html_admin_status_pedido(u,ped,"⚠ Status de pagamento inválido.")); return
            if sr not in STATUS_RASTREIO:
                self._resp(html_admin_status_pedido(u,ped,"⚠ Status de rastreio inválido.")); return
            ped["status_pagamento"]=sp; ped["status_rastreio"]=sr; salv_pedidos()
            self._redir(f"/admin/pedidos?msg={quote_plus(ped['codigo_pedido']+' atualizado! ✅')}")

        # ── Admin: Reclamações ─────────────────────────────────────────────
        elif path=="/admin/reclamacao/responder":
            if not u or u["tipo"]!="admin": self._redir("/"); return
            qs2=parse_qs(urlparse(self.path).query)
            try:    rid=int(qs2.get("id",["0"])[0])
            except: rid=0
            if not rid:
                try: rid=int(_f("id"))
                except: self._redir("/admin/reclamacoes"); return
            recl=buscar_id(reclamacoes,rid)
            if not recl: self._redir("/admin/reclamacoes"); return
            st=_f("status"); resp=_f("resposta")
            if st not in STATUS_RECLAMACAO:
                self._resp(html_admin_reclamacao_responder(u,recl,"⚠ Status inválido.")); return
            recl["status"]=st; recl["resposta"]=resp; salv_reclamacoes()
            self._redir(f"/admin/reclamacoes?msg={quote_plus('Reclamação atualizada! ✅')}")

        else: self._redir("/")


# ═══════════════════════════════════════════════════════════════════════════════
#  PORTA E INICIALIZAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════

def porta_livre(p):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:   s.bind(("0.0.0.0",p)); return True
        except OSError: return False

def encontrar_porta():
    testadas:set=set(); print("\n  Procurando porta livre...")
    while len(testadas)<10001:
        p=random.randint(18000,28000)
        if p in testadas: continue
        testadas.add(p); ok=porta_livre(p)
        print(f"    porta {p} ... {'livre ✓' if ok else 'ocupada ✗'}")
        if ok: return p
    raise RuntimeError("Nenhuma porta livre.")

if __name__=="__main__":
    print("="*55); print("  🛒  LOJA ONLINE DIDÁTICA"); print("="*55)
    inicializar_dados()
    porta=encontrar_porta()
    servidor=HTTPServer(("0.0.0.0",porta),LojaHandler)
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]
    except: ip="0.0.0.0"
    print(f"\n  {'═'*49}")
    print(f"  ✅  Servidor iniciado!")
    print(f"  {'─'*49}")
    print(f"  Acesso  →  http://{ip}:{porta}")
    print(f"  {'─'*49}")
    print(f"  Admin   →  admin@loja.com  /  admin123")
    print(f"  {'─'*49}")
    print(f"  Fotos   →  {os.path.abspath(PASTA_FOTOS)}/")
    print(f"  {'─'*49}")
    print("  Ctrl+C para encerrar.")
    print(f"  {'═'*49}\n")
    try:    servidor.serve_forever()
    except KeyboardInterrupt:
        servidor.shutdown(); print("\n  Encerrado. 👋\n")
