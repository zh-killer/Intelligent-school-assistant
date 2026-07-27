# ====================== 视觉识别 + 语音交互 ======================
import os
import re
import uuid
import subprocess
import gradio as gr
from PIL import Image
from io import BytesIO
import base64
import llm_config
import utils
import config

try:
    import cv2
except ImportError:
    cv2 = None  # OpenCV 是可选依赖（仅拍照+YOLO标注图需要）

# ====================== 图像识别（GLM-4V + YOLO）与摄像头 ======================
_yolo_model = None


def pre_recognize_image(image,
                        prompt="详细描述图片中的所有信息。如果图中出现知名公众人物，"
                               "请直接给出其姓名和身份；不太确定时给出最可能的候选并说明不确定。"):
    """调用智谱GLM-4V多模态模型识别图片内容，返回文字描述"""
    if image is None:
        return ""
    try:
        img_64 = utils.image_to_base64(image)
        response = llm_config.client.chat.completions.create(
            model="glm-4v",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_64}"}}
                    ]
                }
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"图像识别失败：{e}"


def get_yolo_info(img_path):
    """YOLO目标检测：返回 (检测信息文本, 标注框图片路径或None)（模型只加载一次）"""
    global _yolo_model
    try:
        if _yolo_model is None:
            from ultralytics import YOLO
            weights = config.YOLO_WEIGHTS if os.path.exists(config.YOLO_WEIGHTS) else "yolo11n.pt"
            print(f"[YOLO] 加载模型: {weights}")
            _yolo_model = YOLO(weights)

        results = _yolo_model([img_path])
        info_list = []
        boxed_path = None
        for res in results:
            boxes = res.boxes
            if boxes is None or len(boxes) == 0:
                continue
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            cls_ids = boxes.cls.cpu().numpy().astype(int)
            cls_names = res.names
            for i, box in enumerate(xyxy):
                x1, y1, x2, y2 = box
                info_list.append(
                    f"目标{i + 1}：类别={cls_names[cls_ids[i]]}，置信度={confs[i]:.2f}，"
                    f"框坐标[x1:{x1:.0f},y1:{y1:.0f},x2:{x2:.0f},y2:{y2:.0f}]")
            try:
                boxed_path = os.path.splitext(img_path)[0] + "_boxed.png"
                cv2.imwrite(boxed_path, res.plot())
            except Exception as e:
                print(f"[YOLO] 标注图保存失败: {e}")
                boxed_path = None
        return ("。".join(info_list) if info_list else "未检测到目标"), boxed_path
    except ImportError:
        return "未安装ultralytics库，已跳过YOLO检测", None
    except Exception as e:
        return f"YOLO检测失败：{e}", None


def take_photo():
    """拍照：OpenCV调用摄像头，捕获一帧画面并返回PIL图片"""
    if cv2 is None:
        raise gr.Error("OpenCV 未安装，无法使用摄像头。请执行: pip install opencv-python")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise gr.Error("无法打开摄像头！请检查摄像头是否被其他应用占用")

    ret, frame = cap.read()
    cap.release()

    if not ret:
        raise gr.Error("拍照失败！未能获取摄像头画面")

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(frame_rgb)
    print(f"[拍照成功] 图片尺寸: {pil_img.size}")
    return pil_img


# ====================== 语音对话（本地Whisper ASR + Windows TTS） ======================
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
_whisper_model = None


def voice_to_text(audio_path):
    """本地faster-whisper语音转文字"""
    global _whisper_model
    if not audio_path:
        return ""
    try:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print("[语音] 加载Whisper base模型（首次较慢）...")
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, _ = _whisper_model.transcribe(audio_path, language="zh", beam_size=5)
        text = "".join(s.text for s in segs).strip()
        try:
            from zhconv import convert
            text = convert(text, "zh-cn")
        except ImportError:
            pass
        print(f"[语音识别] {text}")
        return text
    except ImportError:
        print("[语音识别] 未安装faster-whisper，请执行: pip install faster-whisper")
        return ""
    except Exception as e:
        print(f"[语音识别] 失败: {e}")
        return ""


def text_to_speech(text):
    """Windows自带中文语音合成，返回wav文件路径（失败返回None）"""
    try:
        clean = re.sub(r"[#*`>\[\]()!【】|]", "", text)
        clean = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", clean)
        clean = re.sub(r"http\S+", "", clean).strip()[:300]
        if not clean:
            return None
        os.makedirs(config.UPLOAD_CACHE_DIR, exist_ok=True)
        out_path = os.path.join(config.UPLOAD_CACHE_DIR, f"tts_{uuid.uuid4().hex[:8]}.wav")
        txt_path = out_path + ".txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(clean)
        ps = ("Add-Type -AssemblyName System.Speech; "
              f"$t = Get-Content -Path '{txt_path}' -Raw -Encoding UTF8; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{out_path}'); $s.Speak($t); $s.Dispose()")
        subprocess.run(["powershell", "-Command", ps], timeout=60, capture_output=True)
        os.remove(txt_path)
        return out_path if os.path.exists(out_path) else None
    except Exception as e:
        print(f"[语音合成] 失败: {e}")
        return None


def on_voice_input(audio_path):
    """麦克风录音结束 → 识别成文字填入输入框"""
    if not audio_path:
        return gr.skip(), None
    text = voice_to_text(audio_path)
    if not text:
        gr.Warning("语音识别没有得到内容：请确认运行环境已安装 faster-whisper"
                   "（pip install faster-whisper zhconv），或录音时离麦克风近一点、说长一点")
        return gr.skip(), None
    return text, None


def tts_reply(enabled, chat_history):
    """朗读开关开着时，把最新一条AI文字回复合成语音"""
    print(f"[TTS] tts_reply 被调用: enabled={enabled}, history_len={len(chat_history) if chat_history else 0}")
    if not enabled or not chat_history:
        return gr.Audio(value=None, visible=False)
    last = chat_history[-1]
    if last.get("role") != "assistant" or not isinstance(last.get("content"), str):
        return gr.Audio(value=None, visible=False)
    audio_path = text_to_speech(last["content"])
    print(f"[TTS] text_to_speech 返回: {audio_path}")
    if audio_path:
        return gr.Audio(value=audio_path, visible=True, autoplay=True)
    return gr.Audio(value=None, visible=False)
