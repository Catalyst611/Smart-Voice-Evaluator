# -*- coding: utf-8 -*-
import mindspore as ms
import mindspore.numpy as mnp
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
import requests, json, time, os, base64, difflib, re
from pydub import AudioSegment
import cv2
from mindspore import nn, ops, Tensor

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdksis.v1.region.sis_region import SisRegion
from huaweicloudsdksis.v1 import SisClient
from huaweicloudsdksis.v1 import model as sis_model

# ❗❗❗ 填入你的真实 AK/SK
HUAWEI_AK = ""  
HUAWEI_SK = ""
UNIVERSAL_REGION = "cn-east-3" 

# ==========================================
# 🌟 视觉防作弊模块
# ==========================================
CHEAT_DIR = "cheat_evidences"
if not os.path.exists(CHEAT_DIR): os.makedirs(CHEAT_DIR)

class EyeStatusDetector(nn.Cell):
    def __init__(self, variance_threshold=150.0):
        super(EyeStatusDetector, self).__init__()
        self.threshold = Tensor(variance_threshold, ms.float32)
        self.reduce_mean = ops.ReduceMean()
        self.square = ops.Square()

    def construct(self, x):
        mean_val = self.reduce_mean(x)
        variance = self.reduce_mean(self.square(x - mean_val))
        return variance > self.threshold

ms.set_context(mode=ms.PYNATIVE_MODE)
ms.set_device("CPU")
ms_eye_detector = EyeStatusDetector(variance_threshold=150.0)

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye_tree_eyeglasses.xml')

# 🌟 新增 assign_id 和 q_id，精准绑定作弊图片
def detect_eye_status_base64(base64_str, student_id="unknown", assign_id="0", q_id="0"):
    try:
        if ',' in base64_str: base64_str = base64_str.split(',')[1]
        img_data = base64.b64decode(base64_str)
        nparr = np.frombuffer(img_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray_frame, scaleFactor=1.3, minNeighbors=5)

        for (x, y, w, h) in faces:
            roi_gray = gray_frame[y:y + int(h / 2), x:x + w]
            eyes = eye_cascade.detectMultiScale(roi_gray, scaleFactor=1.1, minNeighbors=10, minSize=(20, 20))

            if len(eyes) > 0:
                (ex, ey, ew, eh) = eyes[0]
                eye_gray_img = roi_gray[ey:ey + eh, ex:ex + ew]
                ms_input_tensor = Tensor(eye_gray_img, ms.float32)
                eye_open = bool(ms_eye_detector(ms_input_tensor).asnumpy())
                
                if eye_open:
                    # 图片命名规则：学号_作业ID_题号_时间戳.jpg
                    filename = f"{student_id}_a{assign_id}_q{q_id}_{int(time.time())}.jpg"
                    filepath = os.path.join(CHEAT_DIR, filename)
                    cv2.putText(frame, "EYES OPEN DETECTED", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    cv2.imwrite(filepath, frame)
                    return "OPEN"
                else:
                    return "CLOSED"
        return "NO_FACE"
    except Exception as e:
        print(f"视觉推理崩溃: {e}")
        return "ERROR"

# ==========================================
# 🌟 语音评测模块
# ==========================================
def normalize_text(text):
    if not text: return ""
    text = text.lower()
    text = re.sub(r'[^\w\u4e00-\u9fa5]', '', text)
    asr_mistakes = {"516": "56", "412": "42"}
    for w, r in asr_mistakes.items(): text = text.replace(w, r)
    text = text.replace('10', '十')
    num_map = {'0':'零', '1':'一', '2':'二', '3':'三', '4':'四', '5':'五', '6':'六', '7':'七', '8':'八', '9':'九'}
    for k, v in num_map.items(): text = text.replace(k, v)
    return text

def preprocess_and_vectorize(texts):
    cleaned =[normalize_text(t) for t in texts]
    vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 2))
    try: matrix = vectorizer.fit_transform(cleaned).toarray()
    except ValueError: return np.zeros((len(texts), 1)), cleaned
    return matrix, cleaned

def calculate_semantic_correctness_ms_strict(asr_answer, standard_answer):
    norm_asr, norm_std = normalize_text(asr_answer), normalize_text(standard_answer)
    if not norm_asr or not norm_std: return 0.0
    char_ratio = difflib.SequenceMatcher(None, norm_asr, norm_std).ratio()
    matrix, _ = preprocess_and_vectorize([norm_asr, norm_std])
    if matrix.shape[0] < 2 or matrix.shape[1] == 0: ms_score = 0.0
    else:
        va, vb = ms.Tensor(matrix[0], ms.float32), ms.Tensor(matrix[1], ms.float32)
        dp, na, nb = mnp.sum(va * vb), ms.ops.norm(va, ord=2), ms.ops.norm(vb, ord=2)
        ms_score = 0.0 if (na.asnumpy().item()==0 or nb.asnumpy().item()==0) else float((dp/(na*nb)).asnumpy().item())
    final_score = max(char_ratio, ms_score)
    return 1.0 if final_score >= 0.85 else final_score

def calculate_semantic_correctness_ms_loop(asr_answer, standard_answer):
    norm_asr, norm_std = normalize_text(asr_answer), normalize_text(standard_answer)
    if not norm_std or not norm_asr: return 0.0
    matcher = difflib.SequenceMatcher(None, norm_std, norm_asr)
    matched_length = sum([block.size for block in matcher.get_matching_blocks()])
    coverage_rate = matched_length / len(norm_std)
    strict_ratio = matcher.ratio()
    noise_resilient_score = (coverage_rate * 0.7) + (strict_ratio * 0.3)
    matrix, _ = preprocess_and_vectorize([norm_asr, norm_std])
    if matrix.shape[0] < 2 or matrix.shape[1] == 0: ms_score = 0.0
    else:
        va, vb = ms.Tensor(matrix[0], ms.float32), ms.Tensor(matrix[1], ms.float32)
        dp, na, nb = mnp.sum(va * vb), ms.ops.norm(va, ord=2), ms.ops.norm(vb, ord=2)
        ms_score = 0.0 if (na.asnumpy().item()==0 or nb.asnumpy().item()==0) else float((dp/(na*nb)).asnumpy().item())
    final_score = max(noise_resilient_score, ms_score, coverage_rate * 0.9)
    return 1.0 if final_score >= 0.80 else final_score

def generate_diff_html(asr_text, standard_answer):
    norm_asr, norm_std = normalize_text(asr_text), normalize_text(standard_answer)
    matcher = difflib.SequenceMatcher(None, norm_std, norm_asr)
    res =[]
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal': res.append(f'<span style="color:#2ecc71; font-weight:bold;">{norm_asr[j1:j2]}</span>')
        elif tag == 'insert': res.append(f'<span style="color:#e74c3c; text-decoration:line-through;">{norm_asr[j1:j2]}</span>')
        elif tag == 'delete': res.append(f'<span style="color:#f39c12; border-bottom:2px dashed #f39c12;">(漏:{norm_std[i1:i2]})</span>')
        elif tag == 'replace': res.append(f'<span style="color:#e74c3c; font-weight:bold;">{norm_asr[j1:j2]}</span><span style="color:#f39c12; font-size:0.8em;">(应为:{norm_std[i1:i2]})</span>')
    return "".join(res)

def rtts_synthesize_auto(text, output_wav_path, language='zh'):
    try:
        credentials = BasicCredentials(HUAWEI_AK, HUAWEI_SK)
        client = SisClient.new_builder().with_credentials(credentials).with_region(SisRegion.value_of(UNIVERSAL_REGION)).build()
        voice = "english_amy_common" if language == 'en' else "chinese_xiaoyu_common"
        config = sis_model.TtsConfig(audio_format="wav", sample_rate="8000", _property=voice)
        req = sis_model.RunTtsRequest(body=sis_model.PostCustomTTSReq(text=text, config=config))
        resp = client.run_tts(req)
        if resp.result and resp.result.data:
            with open(output_wav_path, "wb") as f: f.write(base64.b64decode(resp.result.data))
            return output_wav_path
    except Exception as e: print(f"TTS Error: {e}")
    return None

def recognize_audio(audio_path, language='zh'):
    if not os.path.exists(audio_path): return ""
    asr_property = "english_16k_common" if language == 'en' else "chinese_16k_general"
    try:
        audio = AudioSegment.from_file(audio_path).set_frame_rate(16000).set_channels(1).set_sample_width(2)
        temp_p = f"tmp_asr_{int(time.time())}.wav"
        audio.export(temp_p, format="wav", parameters=["-acodec", "pcm_s16le"])
        credentials = BasicCredentials(HUAWEI_AK, HUAWEI_SK)
        client = SisClient.new_builder().with_credentials(credentials).with_region(SisRegion.value_of(UNIVERSAL_REGION)).build()
        with open(temp_p, "rb") as f: data_base64 = base64.b64encode(f.read()).decode('utf-8')
        config = sis_model.Config(audio_format="wav", _property=asr_property, add_punc="yes")
        resp = client.recognize_short_audio(sis_model.RecognizeShortAudioRequest(body=sis_model.PostShortAudioReq(config=config, data=data_base64)))
        if os.path.exists(temp_p): os.remove(temp_p)
        return resp.result.text if resp.result and resp.result.text else ""
    except Exception as e: return ""
