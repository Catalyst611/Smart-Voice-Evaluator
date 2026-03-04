# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import os, time, io, json, requests, sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from pydub import AudioSegment
from sis_evaluator import *

app = Flask(__name__)
CORS(app)

AUDIO_DIR = "stored_audio"
if not os.path.exists(AUDIO_DIR): os.makedirs(AUDIO_DIR)
DB_NAME = 'system.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;') 
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, ai_quota INTEGER DEFAULT 20)''')
    c.execute('''CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY AUTOINCREMENT, class_code TEXT UNIQUE NOT NULL, name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_classes (user_id INTEGER, class_id INTEGER, UNIQUE(user_id, class_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL, name TEXT NOT NULL, passing_rate REAL DEFAULT 0.8, is_active INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER, text TEXT, answer TEXT, lang TEXT, order_num INTEGER, baseline_score REAL DEFAULT 85.0, is_fixed_baseline INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS scores (id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER, student_id TEXT, question_id INTEGER, score REAL, asr_text TEXT, audio_path TEXT, is_passed INTEGER DEFAULT 0, UNIQUE(assignment_id, student_id, question_id))''')
    
    # 🌟 新增：专属讲台循环模式成绩表（与家庭作业成绩彻底隔离，互不干扰！）
    c.execute('''CREATE TABLE IF NOT EXISTS loop_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER, student_id TEXT, question_id INTEGER, score REAL, asr_text TEXT, audio_path TEXT, is_passed INTEGER DEFAULT 0, UNIQUE(assignment_id, student_id, question_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS question_bank (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, category TEXT, text TEXT NOT NULL, answer TEXT NOT NULL, baseline_score REAL DEFAULT 85.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit(); conn.close()

init_db()

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username, password, role, class_code = data['username'], data['password'], data['role'], data.get('class_code')
    conn = get_db_connection()
    if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone(): return jsonify({"status": "error", "msg": "已存在"}), 400
    cls = conn.execute("SELECT id FROM classes WHERE class_code=?", (class_code,)).fetchone()
    if role == 'student':
        if not cls: return jsonify({"status": "error", "msg": "班级不存在"}), 400
        class_id = cls['id']
    else:
        class_id = cls['id'] if cls else conn.execute("INSERT INTO classes (class_code, name) VALUES (?, ?)", (class_code, f"班级 {class_code}")).lastrowid
    cur = conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, generate_password_hash(password), role))
    conn.execute("INSERT INTO user_classes (user_id, class_id) VALUES (?, ?)", (cur.lastrowid, class_id))
    conn.commit(); conn.close()
    return jsonify({"status": "success"})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username=?", (data['username'],)).fetchone()
    conn.close()
    if user and check_password_hash(user['password'], data['password']): return jsonify({"status": "success", "username": user['username'], "role": user['role'], "ai_quota": user['ai_quota']})
    return jsonify({"status": "error", "msg": "错误"}), 401

@app.route('/api/teacher/classes', methods=['GET'])
def get_teacher_classes():
    conn = get_db_connection()
    res = conn.execute('''SELECT c.id, c.class_code, c.name FROM classes c JOIN user_classes uc ON c.id = uc.class_id JOIN users u ON u.id = uc.user_id WHERE u.username = ?''', (request.args.get('username'),)).fetchall()
    conn.close(); return jsonify([dict(c) for c in res])

@app.route('/api/teacher/join_class', methods=['POST'])
def teacher_join_class():
    username, class_code = request.json['username'], request.json['class_code']
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    cls = conn.execute("SELECT id FROM classes WHERE class_code=?", (class_code,)).fetchone()
    class_id = cls['id'] if cls else conn.execute("INSERT INTO classes (class_code, name) VALUES (?, ?)", (class_code, f"班级 {class_code}")).lastrowid
    try: conn.execute("INSERT INTO user_classes (user_id, class_id) VALUES (?, ?)", (user['id'], class_id)); conn.commit()
    except: pass
    conn.close(); return jsonify({"status": "success"})

@app.route('/api/assignment/current', methods=['GET'])
def current_assignment():
    conn = get_db_connection()
    assign = conn.execute("SELECT * FROM assignments WHERE class_id=? AND is_active=1", (request.args.get('class_id'),)).fetchone()
    conn.close(); return jsonify(dict(assign) if assign else {})

@app.route('/api/assignments', methods=['GET'])
def get_all_assignments():
    conn = get_db_connection()
    assigns = conn.execute("SELECT * FROM assignments WHERE class_id=? ORDER BY id DESC", (request.args.get('class_id'),)).fetchall()
    conn.close(); return jsonify([dict(a) for a in assigns])

@app.route('/api/assignment/new', methods=['POST'])
def new_assignment():
    data = request.json
    class_id, name, passing_rate = data['class_id'], data.get('name', "新建作业"), float(data.get('passing_rate', 80))/100.0
    conn = get_db_connection()
    conn.execute("UPDATE assignments SET is_active=0 WHERE class_id=?", (class_id,))
    conn.execute("INSERT INTO assignments (class_id, name, passing_rate, is_active) VALUES (?, ?, ?, 1)", (class_id, name, passing_rate))
    conn.commit(); conn.close()
    return jsonify({"status": "success"})

@app.route('/api/student/current_assignment', methods=['GET'])
def student_current_assignment():
    conn = get_db_connection()
    res = conn.execute('''SELECT a.* FROM assignments a JOIN classes c ON a.class_id = c.id JOIN user_classes uc ON c.id = uc.class_id JOIN users u ON u.id = uc.user_id WHERE u.username=? AND a.is_active=1''', (request.args.get('username'),)).fetchone()
    conn.close(); return jsonify(dict(res) if res else {})

# 🌟 核心修复：根据传来的 mode，智能切换查询 scores 表还是 loop_scores 表！
@app.route('/api/admin/scores')
def get_scores():
    assign_id, mode = request.args.get('assignment_id'), request.args.get('mode', 'normal')
    if not assign_id: return jsonify([])
    
    table_name = 'loop_scores' if mode == 'loop' else 'scores'
    
    conn = get_db_connection()
    assign = conn.execute("SELECT class_id FROM assignments WHERE id=?", (assign_id,)).fetchone()
    if not assign: conn.close(); return jsonify([])
    class_id = assign['class_id']
    qs = conn.execute("SELECT id FROM questions WHERE assignment_id=? ORDER BY order_num ASC", (assign_id,)).fetchall()
    q_ids = [q['id'] for q in qs]
    students = conn.execute('''SELECT u.username FROM users u JOIN user_classes uc ON u.id=uc.user_id WHERE uc.class_id=? AND u.role='student' ORDER BY u.username ASC''', (class_id,)).fetchall()
    all_students = [s['username'] for s in students]
    
    scores = conn.execute(f"SELECT * FROM {table_name} WHERE assignment_id=?", (assign_id,)).fetchall()
    score_map = {}
    for s in scores:
        sid, qid = s['student_id'], s['question_id']
        if sid not in score_map: score_map[sid] = {}
        score_map[sid][qid] = dict(s)
        
    submitted_students =[sid for sid in all_students if sid in score_map]
    unsubmitted_students =[sid for sid in all_students if sid not in score_map]
    result_list =[]
    for sid in submitted_students:
        for q_id in q_ids:
            if q_id in score_map[sid]: result_list.append(score_map[sid][q_id])
            else: result_list.append({"student_id": sid, "question_id": q_id, "score": "-", "is_passed": None, "audio_path": None, "is_partial_missing": True})
    for sid in unsubmitted_students:
        result_list.append({"student_id": sid, "question_id": "-", "score": "-", "is_passed": None, "audio_path": None, "is_total_missing": True})
    conn.close()
    return jsonify(result_list)

@app.route('/api/admin/class_overview')
def class_overview():
    assign_id, mode = request.args.get('assignment_id'), request.args.get('mode', 'normal')
    if not assign_id: return jsonify([])
    table_name = 'loop_scores' if mode == 'loop' else 'scores'
    
    conn = get_db_connection()
    assign = conn.execute("SELECT class_id FROM assignments WHERE id=?", (assign_id,)).fetchone()
    if not assign: conn.close(); return jsonify([])
    
    qs = conn.execute("SELECT id, order_num FROM questions WHERE assignment_id=? ORDER BY order_num ASC", (assign_id,)).fetchall()
    q_map = {q['id']: idx+1 for idx, q in enumerate(qs)}
    total_qs = len(qs)
    students = conn.execute('''SELECT u.username FROM users u JOIN user_classes uc ON u.id=uc.user_id WHERE uc.class_id=? AND u.role='student' ORDER BY u.username ASC''', (assign['class_id'],)).fetchall()
    scores = conn.execute(f"SELECT * FROM {table_name} WHERE assignment_id=?", (assign_id,)).fetchall()
    
    score_map = {}
    for s in scores:
        if s['student_id'] not in score_map: score_map[s['student_id']] = []
        score_map[s['student_id']].append(dict(s))
    overview = []
    for stu in students:
        sid = stu['username']
        stu_scores = score_map.get(sid,[])
        if len(stu_scores) == 0:
            status, detail = "未交", "-"
        else:
            submitted_qids = [s['question_id'] for s in stu_scores]
            missing_idx =[str(q_map[qid]) for qid in q_map if qid not in submitted_qids]
            failed_idx = [str(q_map[s['question_id']]) for s in stu_scores if not s['is_passed']]
            issues =[]
            if missing_idx: issues.append(f"漏交第 {','.join(missing_idx)} 题")
            if failed_idx: issues.append(f"第 {','.join(failed_idx)} 题不合格")
            if issues: status, detail = "不合格", "；".join(issues)
            else: status, detail = ("全部合格", "完美过关") if len(stu_scores) == total_qs else ("不合格", "异常")
        overview.append({"student_id": sid, "status": status, "detail": detail})
    conn.close(); return jsonify(overview)

@app.route('/api/questions', methods=['GET', 'POST'])
def handle_questions():
    assign_id = request.args.get('assignment_id')
    conn = get_db_connection()
    if request.method == 'POST':
        d = request.json
        conn.execute("INSERT INTO questions (assignment_id, text, answer, lang, order_num, baseline_score, is_fixed_baseline) VALUES (?, ?, ?, ?, ?, ?, ?)", (assign_id, d['text'], d['answer'], d.get('lang', 'zh'), int(time.time()), d.get('baseline_score', 85.0), d.get('is_fixed_baseline', 0)))
        conn.commit(); conn.close(); return jsonify({"status": "ok"})
    qs = conn.execute("SELECT * FROM questions WHERE assignment_id=? ORDER BY order_num ASC", (assign_id,)).fetchall()
    conn.close(); return jsonify([dict(q) for q in qs])

@app.route('/api/questions/<int:q_id>', methods=['DELETE'])
def update_delete_question(q_id):
    conn = get_db_connection(); conn.execute("DELETE FROM questions WHERE id=?", (q_id,)); conn.commit(); conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/bank', methods=['GET', 'POST', 'DELETE'])
def manage_bank():
    conn = get_db_connection()
    if request.method == 'GET':
        qs = conn.execute("SELECT * FROM question_bank WHERE subject=? ORDER BY id DESC", (request.args.get('subject', 'zh'),)).fetchall()
        conn.close(); return jsonify([dict(q) for q in qs])
    if request.method == 'POST':
        d = request.json
        conn.execute("INSERT INTO question_bank (subject, category, text, answer, baseline_score) VALUES (?, ?, ?, ?, ?)", (d['subject'], d.get('category','默认分类'), d['text'], d['answer'], float(d.get('baseline_score', 85.0))))
        conn.commit(); conn.close(); return jsonify({"status": "success"})
    if request.method == 'DELETE':
        conn.execute("DELETE FROM question_bank WHERE id=?", (request.args.get('id'),)); conn.commit(); conn.close()
        return jsonify({"status": "success"})

# 🌟 新增：批量删除当前科目下所有题库的接口
@app.route('/api/bank/clear', methods=['DELETE'])
def clear_bank():
    subject = request.args.get('subject')
    conn = get_db_connection()
    conn.execute("DELETE FROM question_bank WHERE subject=?", (subject,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/bank/import_txt', methods=['POST'])
def import_txt():
    txt_file = request.files['file']
    subject, category = request.form['subject'], request.form['category']
    content = txt_file.read().decode('utf-8')
    conn = get_db_connection()
    count = 0
    for line in content.split('\n'):
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 2:
            base = float(parts[2]) if len(parts)>2 and parts[2].replace('.','').isdigit() else 85.0
            conn.execute("INSERT INTO question_bank (subject, category, text, answer, baseline_score) VALUES (?, ?, ?, ?, ?)", (subject, category, parts[0], parts[1], base))
            count += 1
    conn.commit(); conn.close()
    return jsonify({"status": "success", "count": count})

@app.route('/api/ai/generate', methods=['POST'])
def ai_generate():
    data = request.json
    username, keyword, subject, count = data['username'], data['keyword'], data['subject'], data.get('count', 3) 
    conn = get_db_connection()
    user = conn.execute("SELECT id, ai_quota FROM users WHERE username=?", (username,)).fetchone()
    if not user or user['ai_quota'] <= 0: conn.close(); return jsonify({"status": "error", "msg": "额度用完"}), 400
    new_quota = user['ai_quota'] - 1
    conn.execute("UPDATE users SET ai_quota=? WHERE id=?", (new_quota, user['id'])); conn.commit(); conn.close()

    api_key = ""  # 👈 替换真实Key
    api_url = ""

    if subject == 'en':
        system_prompt = f"""你是一个资深中小学英语教研专家。请生成 {count} 道英语测试题。
        【绝对铁律】：
        1. 词汇精准降维：题目单词必须严格匹配老师要求的年龄/学段，绝不超纲！
        2. 课本场景提取：精准定位老师提问的章节对话，答案必须是课本原文应答！拒绝“本节第一句话是什么”这类生硬旁白，伪装成身临其境的对话。
        3. 必须返回JSON。
        4. 答案必须纯英文（数字转英文单词），保留正常英文标点！
        格式：{{"data":[ {{"text":"How are you?", "answer":"I am fine, thank you."}} ]}}"""
    elif subject == 'science':
        system_prompt = f"""你是一个资深中小学数理化教研专家。请生成 {count} 道数理化概念背诵测试题。
        【绝对铁律】：
        1. 公式与定义必须严谨无误，若是集合或范围(如质数)必须全量穷举，不准省略！
        2. 必须返回JSON。
        3. 标准答案必须纯汉字读音（'a²'写'a的平方'，'='写'等于'），保留正常中文标点。
        格式：{{"data":[ {{"text":"勾股定理？", "answer":"a的平方加b的平方，等于c的平方。"}} ]}}"""
    else:
        system_prompt = f"""你是一个资深中小学语文教研专家。请生成 {count} 道语文背诵测试题。
        【绝对铁律】：
        1. 封杀阅读理解：绝对不允许出现“表达了什么感情”的提问！必须以“请背诵XXX”提问。
        2. 强制全文/全段：严格按照课标提取整段，绝对不允许使用省略号！
        3. 必须返回JSON。
        4. 答案必须纯汉字，保留正确中文标点！
        格式：{{"data":[ {{"text":"背诵静夜思。", "answer":"床前明月光，疑是地上霜。举头望明月，低头思故乡。"}} ]}}"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"model": "deepseek-ai/DeepSeek-V3", "messages":[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"知识点：【{keyword}】。"}], "temperature": 0.7, "response_format": {"type": "json_object"}}

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=90)
        response.raise_for_status() 
        txt = response.json()['choices'][0]['message']['content'].strip()
        if txt.startswith("```json"): txt = txt[7:]
        if txt.startswith("```"): txt = txt[3:]
        if txt.endswith("```"): txt = txt[:-3]
        return jsonify({"status": "success", "data": json.loads(txt.strip()).get("data",[]), "remain_quota": new_quota})
    except Exception as e:
        conn = get_db_connection(); conn.execute("UPDATE users SET ai_quota=? WHERE id=?", (new_quota+1, user['id'])); conn.commit(); conn.close()
        return jsonify({"status": "error", "msg": f"失败: {str(e)}"}), 500

@app.route('/api/students/match', methods=['GET'])
def match_student():
    voice_text = request.args.get('text', '')
    class_id = request.args.get('class_id')
    if not voice_text: return jsonify({"status": "error", "msg": "未听到声音"})
    conn = get_db_connection()
    students = conn.execute('''SELECT u.username FROM users u JOIN user_classes uc ON u.id=uc.user_id WHERE uc.class_id=? AND u.role='student' ''', (class_id,)).fetchall()
    conn.close()
    
    import re, difflib
    vt = voice_text.lower()
    num_map = {'一':'1','幺':'1','二':'2','两':'2','三':'3','四':'4','五':'5','六':'6','七':'7','八':'8','九':'9','零':'0'}
    for k, v in num_map.items(): vt = vt.replace(k, v)
    vt = re.sub(r'[^\w]', '', vt) 
    
    best_match, max_score = None, 0
    for s in students:
        uname = str(s['username']).lower()
        if uname in vt or vt in uname: return jsonify({"status": "success", "username": s['username']})
        score = difflib.SequenceMatcher(None, uname, vt).ratio()
        if score > max_score: max_score, best_match = score, s['username']
            
    if best_match and max_score > 0.35: return jsonify({"status": "success", "username": best_match})
    return jsonify({"status": "error", "msg": "未找到匹配"})

@app.route('/recognize_and_evaluate', methods=['POST'])
def evaluate():
    audio_file, s_id, q_id = request.files['user_audio'], request.form['student_id'], request.form['question_id']
    lang, standard_answer = request.form['language'], request.form['standard_answer']
    
    # 🌟 核心：判定是课后作业(normal)还是讲台循环(loop)，存储到不同表中
    mode = request.form.get('mode', 'normal')
    table_name = 'loop_scores' if mode == 'loop' else 'scores'
    
    if lang == 'science': lang = 'zh'
    conn = get_db_connection()
    curr = conn.execute('''SELECT a.id, a.passing_rate FROM assignments a JOIN user_classes uc ON a.class_id = uc.class_id JOIN users u ON u.id = uc.user_id WHERE u.username=? AND a.is_active=1''', (s_id,)).fetchone()
    assign_id, passing_rate = (curr['id'], curr['passing_rate']) if curr else (0, 0.8)
    
    temp_name = f"{s_id}_{mode}_{assign_id}_{q_id}_{int(time.time())}.wav"
    temp_path = os.path.join(AUDIO_DIR, temp_name)
    audio_file.save(temp_path)
    try: AudioSegment.from_file(temp_path).set_frame_rate(8000).set_channels(1).export(temp_path, format="wav")
    except: pass
    
    raw_asr_text = recognize_audio(temp_path, lang)
    diff_html = generate_diff_html(raw_asr_text, standard_answer)
    asr_text = normalize_text(raw_asr_text)
    
    score_100 = round(calculate_semantic_correctness_ms(asr_text, standard_answer) * 100, 2)
    q_info = conn.execute("SELECT baseline_score, is_fixed_baseline FROM questions WHERE id=?", (q_id,)).fetchone()
    current_baseline, is_fixed = q_info['baseline_score'], q_info['is_fixed_baseline']
    threshold = current_baseline * passing_rate
    is_passed_now = 1 if score_100 >= threshold else 0
    
    old = conn.execute(f"SELECT score, audio_path, is_passed FROM {table_name} WHERE assignment_id=? AND student_id=? AND question_id=?", (assign_id, s_id, q_id)).fetchone()
    final_is_passed = 1 if (is_passed_now or (old and old['is_passed'])) else 0
    
    is_best = False
    if not old or score_100 > old['score']:
        is_best = True
        if old and os.path.exists(os.path.join(AUDIO_DIR, old['audio_path'])): os.remove(os.path.join(AUDIO_DIR, old['audio_path']))
        
        # 存入对应的表
        conn.execute(f'''INSERT INTO {table_name} (assignment_id, student_id, question_id, score, asr_text, audio_path, is_passed) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(assignment_id, student_id, question_id) DO UPDATE SET score=excluded.score, asr_text=excluded.asr_text, audio_path=excluded.audio_path, is_passed=excluded.is_passed''', (assign_id, s_id, q_id, score_100, asr_text, temp_name, final_is_passed))
        
        # 如果是家庭作业模式，才会推高全局基准线（循环模式通常环境嘈杂，不作为标准基准）
        if mode == 'normal' and score_100 > current_baseline and not is_fixed: 
            conn.execute("UPDATE questions SET baseline_score=? WHERE id=?", (score_100, q_id))
        conn.commit()
    else: os.remove(temp_path) 
    conn.close()
    return jsonify({"asr_text": asr_text, "score": score_100, "is_best": is_best, "is_passed": final_is_passed, "diff_html": diff_html})

@app.route('/api/recognize_command', methods=['POST'])
def recognize_command():
    audio_file, temp_path = request.files['user_audio'], os.path.join(AUDIO_DIR, f"cmd_{int(time.time())}.wav")
    audio_file.save(temp_path)
    text = recognize_audio(temp_path, 'zh')
    if os.path.exists(temp_path): os.remove(temp_path)
    return jsonify({"text": text})

@app.route('/api/audio/<filename>')
def get_audio(filename):
    path = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(path): return "Not Found", 404
    file_size, range_header = os.path.getsize(path), request.headers.get('Range', None)
    if not range_header: return send_file(path, mimetype="audio/wav", conditional=True)
    byte1, byte2 = 0, None
    m = range_header.replace('bytes=', '').split('-')
    if m[0]: byte1 = int(m[0])
    if len(m) > 1 and m[1]: byte2 = int(m[1])
    if byte2 is None: byte2 = file_size - 1
    length = byte2 - byte1 + 1
    with open(path, 'rb') as f: f.seek(byte1); data = f.read(length)
    rv = Response(data, 206, mimetype='audio/wav', direct_passthrough=True)
    rv.headers.add('Content-Range', f'bytes {byte1}-{byte2}/{file_size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv

@app.route('/synthesize_question', methods=['POST'])
def synthesize():
    data = request.json
    lang = 'zh' if data.get('language') == 'science' else data.get('language', 'zh')
    temp_wav = os.path.join(AUDIO_DIR, f"tts_{int(time.time())}.wav")
    path = rtts_synthesize_auto(data['text'], temp_wav, lang)
    if path and os.path.exists(path): return send_file(path, mimetype="audio/wav")
    return "TTS Error", 500

@app.route('/')
def index(): return send_file('login.html', max_age=3600)
@app.route('/<path:filename>')
def serve_html(filename):
    if os.path.exists(filename): return send_file(filename)
    return "Not Found", 404
