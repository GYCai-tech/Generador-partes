# -*- coding: utf-8 -*-
import os, io
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
import pyodbc
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, HRFlowable, Image
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)

DB_SERVER   = os.environ.get('DB_SERVER',   '10.0.0.6')
DB_NAME     = os.environ.get('DB_NAME',     'GOMEZYCRESPO')
DB_USER     = os.environ.get('DB_USER',     'gestor_incidencias')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'Auria1973')
DB_DRIVER   = os.environ.get('DB_DRIVER',  'SQL Server')

DB_CONN = (
    f'DRIVER={{{DB_DRIVER}}};'
    f'SERVER={DB_SERVER};'
    f'DATABASE={DB_NAME};'
    f'UID={DB_USER};'
    f'PWD={DB_PASSWORD};'
    f'TrustServerCertificate=yes;'
)

# Colors – B&W optimised (prints cleanly without colour)
BLACK  = colors.black
WHITE  = colors.white
DGRAY  = colors.HexColor('#222222')   # section headers
LGRAY  = colors.HexColor('#eeeeee')   # sub-headers / alternating rows
MGRAY  = colors.HexColor('#999999')   # grid lines
# kept for compatibility but mapped to neutral tones
BLUE   = DGRAY
ORANGE = BLACK

def get_conn():
    return pyodbc.connect(DB_CONN)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/siguiente-numero')
def siguiente_numero():
    conn = get_conn()
    cursor = conn.cursor()
    year2 = str(datetime.now().year)[2:]
    cursor.execute("SELECT ISNULL(MAX(NumParte), 0) + 1 FROM Partes")
    num = cursor.fetchone()[0]
    conn.close()
    return jsonify({'num': num, 'formato': f'{num:02d}/{year2}'})

@app.route('/api/buscar')
def buscar():
    q = request.args.get('q', '').strip()
    tipo = request.args.get('tipo', 'todo')
    if not q:
        return jsonify([])

    conn = get_conn()
    cursor = conn.cursor()
    conditions, params = [], []

    if tipo == 'numero':
        try:
            conditions.append('p.NumPedido = ?')
            params.append(int(q))
        except ValueError:
            return jsonify([])
    elif tipo == 'cliente':
        conditions.append('c.Cliente LIKE ?')
        params.append(f'%{q}%')
    else:
        try:
            num = int(q)
            conditions.append('(p.NumPedido = ? OR c.Cliente LIKE ?)')
            params.extend([num, f'%{q}%'])
        except ValueError:
            conditions.append('c.Cliente LIKE ?')
            params.append(f'%{q}%')

    where = ' AND '.join(conditions)
    cursor.execute(f"""
        SELECT TOP 20
            p.IdPedido, p.NumPedido, p.Fecha, p.FechaSalida,
            p.IdCliente, p.ContactoLlamada, p.Observaciones,
            o.DirEntrega, o.Atencion,
            c.Cliente, c.Direccion, c.Ciudad, c.NumTelefono
        FROM Pedidos_Cli_Cabecera p
        LEFT JOIN Pedidos_Cli_Cab_Otros o ON p.IdPedido = o.IdPedido
        LEFT JOIN Clientes_Datos c ON p.IdCliente = c.IdCliente
        WHERE {where}
        ORDER BY p.NumPedido DESC
    """, params)

    cols = [d[0] for d in cursor.description]
    results = []
    for row in cursor.fetchall():
        d = dict(zip(cols, row))
        fecha = d['Fecha'] or d['FechaSalida']
        d['FechaFormateada'] = fecha.strftime('%d-%m-%Y') if fecha else ''
        dir_entrega = (d['DirEntrega'] or '').strip()
        if dir_entrega:
            d['DireccionTrabajo'] = dir_entrega
            d['LocalidadTrabajo'] = ''
        else:
            d['DireccionTrabajo'] = (d['Direccion'] or '').strip()
            d['LocalidadTrabajo'] = (d['Ciudad'] or '').strip()
        d['Contacto'] = (d['ContactoLlamada'] or d['NumTelefono'] or '').strip()
        results.append(d)

    conn.close()
    return jsonify(results)


def build_pdf(data):
    buf = io.BytesIO()
    W, H = A4
    M = 13 * mm

    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=M, rightMargin=M,
        topMargin=11*mm, bottomMargin=11*mm)

    usable_w = W - 2*M

    def st(size, bold=False, color=BLACK, align=TA_LEFT):
        return ParagraphStyle('x',
            fontName='Helvetica-Bold' if bold else 'Helvetica',
            fontSize=size, textColor=color, alignment=align, leading=size*1.25)

    def p(text, size=8, bold=False, color=BLACK, align=TA_LEFT):
        safe = str(text or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        return Paragraph(safe, st(size, bold, color, align))

    def lbl(text):
        return p(text.upper(), 7, bold=True, color=colors.HexColor('#6b7280'))

    def val(text, size=9, bold=False, color=BLACK):
        return p(str(text or ''), size, bold, color)

    def sec_hdr(text):
        t = Table([[p(text.upper(), 7, bold=True, color=WHITE)]], colWidths=[usable_w])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),BLUE),
            ('LEFTPADDING',(0,0),(-1,-1),6),
            ('TOPPADDING',(0,0),(-1,-1),3),
            ('BOTTOMPADDING',(0,0),(-1,-1),3),
        ]))
        return t

    PAD = [('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
           ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),
           ('VALIGN',(0,0),(-1,-1),'MIDDLE')]

    story = []

    # ── HEADER ──────────────────────────────────────────
    albaran     = data.get('albaran','')
    presupuesto = data.get('presupuesto','')

    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
    logo_img = Image(logo_path, width=44*mm, height=11*mm)

    hdr = Table([[
        logo_img,
        p('PARTE DE TRABAJO', 13, bold=True, color=BLACK, align=TA_CENTER),
        p('', 7, color=BLACK, align=TA_RIGHT),
    ]], colWidths=[50*mm, usable_w-80*mm, 30*mm])
    hdr.setStyle(TableStyle(PAD + [
        ('BACKGROUND',(0,0),(-1,-1),WHITE),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LINEBELOW',(0,0),(-1,-1),1.5,BLACK),
    ]))
    story.append(hdr)

    alb = Table([[
        lbl('Albarán de trabajo nº'), val(albaran, 11, bold=True, color=BLACK),
        lbl('Presupuesto'), val(presupuesto, 9, color=BLACK),
    ]], colWidths=[45*mm, 55*mm, 32*mm, usable_w-132*mm])
    alb.setStyle(TableStyle(PAD + [
        ('BACKGROUND',(0,0),(-1,-1),LGRAY),
        ('LINEBELOW',(0,0),(-1,-1),0.5,MGRAY),
        ('LINEBEFORE',(2,0),(2,-1),0.5,MGRAY),
    ]))
    story.append(alb)

    # ── DATOS PRINCIPALES ───────────────────────────────
    cliente   = data.get('cliente','')
    direccion = data.get('direccion','')
    localidad = data.get('localidad','')
    contacto  = data.get('contacto','')
    gps       = data.get('gps','')
    fecha     = data.get('fecha','')

    def frow(cells_widths, bg=WHITE, line_below=True):
        cells = [[c for c,_ in cells_widths]]
        widths = [w for _,w in cells_widths]
        t = Table(cells, colWidths=widths)
        s = PAD + [('BACKGROUND',(0,0),(-1,-1),bg)]
        if line_below:
            s.append(('LINEBELOW',(0,0),(-1,-1),0.5,MGRAY))
        t.setStyle(TableStyle(s))
        return t

    story.append(frow([(lbl('Cliente'), 22*mm), (val(cliente,11,bold=True,color=BLACK), usable_w-22*mm)]))
    story.append(frow([
        (lbl('Dirección de trabajo'), 43*mm),
        (val(direccion,9), usable_w*0.45),
        (val(localidad,9,bold=True), usable_w-43*mm-usable_w*0.45),
    ]))
    story.append(frow([(lbl('Contacto'), 22*mm), (val(contacto,9), usable_w-22*mm)]))
    story.append(frow([(lbl('GPS'), 22*mm), (val(gps,9,bold=True), usable_w-22*mm)], bg=LGRAY))
    story.append(frow([(lbl('Fecha'), 22*mm), (val(fecha,9,bold=True), usable_w-22*mm)]))
    story.append(Spacer(1, 2*mm))

    # ── FACTURACIÓN ─────────────────────────────────────
    story.append(sec_hdr('Facturación'))

    conceptos = data.get('conceptos', [])
    if not conceptos:
        conceptos = [{'cant':'', 'concepto':''}]

    cw = [16*mm, usable_w-16*mm]
    c_hdr = Table([[lbl('Cant.'), lbl('Conceptos de trabajo')]], colWidths=cw)
    c_hdr.setStyle(TableStyle(PAD + [
        ('BACKGROUND',(0,0),(-1,-1),LGRAY),
        ('LINEBELOW',(0,0),(-1,-1),0.5,MGRAY),
        ('LINEBEFORE',(1,0),(1,-1),0.5,MGRAY),
    ]))
    story.append(c_hdr)

    c_rows = [[str(c.get('cant','')), str(c.get('concepto',''))] for c in conceptos]
    # pad to at least 7 rows
    while len(c_rows) < 7:
        c_rows.append(['',''])
    c_t = Table(c_rows, colWidths=cw, rowHeights=[7*mm]*len(c_rows))
    c_t.setStyle(TableStyle(PAD + [
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('LINEBELOW',(0,0),(-1,-1),0.3,MGRAY),
        ('LINEBEFORE',(1,0),(1,-1),0.5,MGRAY),
    ]))
    story.append(c_t)
    story.append(Spacer(1, 2*mm))

    # ── OPERARIOS ───────────────────────────────────────
    story.append(sec_hdr('Nombre Operarios'))
    operarios = data.get('operarios', [{'nombre':''}]*2)
    op_rows = [[f'{i+1}º', op.get('nombre','') if isinstance(op,dict) else ''] for i,op in enumerate(operarios)]
    if not op_rows:
        op_rows = [['1º',''],['2º','']]
    op_t = Table(op_rows, colWidths=[12*mm, usable_w-12*mm], rowHeights=[7*mm]*len(op_rows))
    op_t.setStyle(TableStyle(PAD + [
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('TEXTCOLOR',(0,0),(0,-1),BLACK),
        ('LINEBELOW',(0,0),(-1,-1),0.3,MGRAY),
    ]))
    story.append(op_t)
    story.append(Spacer(1, 2*mm))

    # ── HORAS ───────────────────────────────────────────
    story.append(sec_hdr('Horas Empleadas'))
    col5 = usable_w / 5
    h_hdr = Table([[lbl('Día'), lbl('H. Inicio'), lbl('H. Final'), lbl('Operarios'), lbl('Definición del trabajo')]],
                  colWidths=[col5]*5)
    h_hdr.setStyle(TableStyle(PAD + [
        ('BACKGROUND',(0,0),(-1,-1),LGRAY),
        ('LINEBELOW',(0,0),(-1,-1),0.5,MGRAY),
        ('LINEBEFORE',(1,0),(-1,-1),0.3,MGRAY),
    ]))
    story.append(h_hdr)

    horas = data.get('horas', [])
    h_rows = [[h.get('dia',''),h.get('inicio',''),h.get('final',''),h.get('operarios',''),h.get('definicion','')] for h in horas]
    while len(h_rows) < 8:
        h_rows.append(['','','','',''])
    h_t = Table(h_rows, colWidths=[col5]*5, rowHeights=[7*mm]*len(h_rows))
    h_t.setStyle(TableStyle(PAD + [
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('LINEBELOW',(0,0),(-1,-1),0.3,MGRAY),
        ('LINEBEFORE',(1,0),(-1,-1),0.3,MGRAY),
    ]))
    story.append(h_t)
    story.append(Spacer(1, 2*mm))

    # ── OBSERVACIONES ───────────────────────────────────
    story.append(sec_hdr('Observaciones'))
    obs_text = data.get('observaciones','')
    obs_t = Table([[val(obs_text,8)],[''],[''],['']],
                  colWidths=[usable_w], rowHeights=[8*mm,7*mm,7*mm,7*mm])
    obs_t.setStyle(TableStyle(PAD + [
        ('LINEBELOW',(0,0),(-1,-1),0.3,MGRAY),
    ]))
    story.append(obs_t)
    story.append(Spacer(1, 2*mm))

    # ── GASTOS IMPUTABLES ───────────────────────────────
    story.append(sec_hdr('Gastos Imputables'))

    gastos = data.get('gastos', [])
    half = usable_w / 2
    gc = [14*mm, half-34*mm, 20*mm]

    def gastos_pair(g1, g2, n1, n2):
        def gblock(g, n):
            g = g or {}
            return [
                [p(f'{n}º Operario', 7, bold=True, color=BLACK), lbl('Cant.'), lbl('Total')],
                [p('Km.',8), val(g.get('km_cant',''),8), val(g.get('km_total',''),8)],
                [p('Dietas',8), val(g.get('dietas_cant',''),8), val(g.get('dietas_total',''),8)],
                [p('Comidas',8), val(g.get('comidas_cant',''),8), val(g.get('comidas_total',''),8)],
            ]
        rows1 = gblock(g1, n1)
        rows2 = gblock(g2, n2)
        merged = [r1 + r2 for r1, r2 in zip(rows1, rows2)]
        t = Table(merged, colWidths=gc+gc, rowHeights=[6*mm,6*mm,6*mm,6*mm])
        t.setStyle(TableStyle(PAD + [
            ('FONTSIZE',(0,0),(-1,-1),7),
            ('LINEBELOW',(0,0),(-1,-1),0.3,MGRAY),
            ('LINEBEFORE',(3,0),(3,-1),1,MGRAY),
            ('BACKGROUND',(0,0),(0,0),LGRAY),
            ('BACKGROUND',(3,0),(3,0),LGRAY),
        ]))
        return t

    num_gastos = max(len(gastos), 2)
    for i in range(0, num_gastos, 2):
        g1 = gastos[i]   if i   < len(gastos) else {}
        g2 = gastos[i+1] if i+1 < len(gastos) else {}
        story.append(gastos_pair(g1, g2, i+1, i+2))
    story.append(Spacer(1, 2*mm))

    # ── FIRMAS ──────────────────────────────────────────
    story.append(sec_hdr('Firmas'))
    firmas_t = Table(
        [[lbl('Operarios:'),'',lbl('VºBº Cliente:'),''],
         ['','','',''],['','','',''],['','','',''],['','','','']],
        colWidths=[28*mm, usable_w/2-28*mm, 28*mm, usable_w/2-28*mm],
        rowHeights=[5*mm,9*mm,9*mm,9*mm,9*mm],
    )
    firmas_t.setStyle(TableStyle(PAD + [
        ('LINEBELOW',(0,1),(1,-1),0.3,MGRAY),
        ('LINEBELOW',(2,1),(3,-1),0.3,MGRAY),
        ('LINEBEFORE',(2,0),(2,-1),1,MGRAY),
    ]))
    story.append(firmas_t)

    doc.build(story)
    buf.seek(0)
    return buf


@app.route('/api/generar', methods=['POST'])
def generar():
    data = request.get_json()
    albaran = data.get('albaran', 'parte')
    buf = build_pdf(data)
    filename = f"Parte_{albaran.replace('/', '-')}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype='application/pdf')

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', debug=debug, port=5000)
