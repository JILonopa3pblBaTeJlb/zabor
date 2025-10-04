# antibayan.py
import io
import os
import numpy as np
from PIL import Image
import hashlib

# ========== Быстрая фича-хеш функция ==========
def quick_fingerprint(img_bytes: bytes) -> str:
    """
    Возвращает короткий хеш (64 бита) изображения.
    Устойчив к ресайзу, jpeg-компрессии, легким цветовым искажениям.
    """
    if not img_bytes or len(img_bytes) == 0:
        print("[fingerprint] ❌ Пустые байты")
        return None
    
    try:
        # Пытаемся открыть как изображение
        img = Image.open(io.BytesIO(img_bytes))
        
        # Проверяем, что это валидное изображение
        img.verify()
        
        # Открываем заново после verify (verify() делает файл непригодным)
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        img = img.resize((8, 8), Image.Resampling.LANCZOS)
        arr = np.asarray(img, dtype=np.float32)
        mean_val = arr.mean()
        bits = (arr > mean_val).astype(np.uint8)
        bitstring = "".join(map(str, bits.flatten()))
        # в 16-ричный вид (64 бита → 16 hex)
        hash_result = hashlib.sha1(bitstring.encode()).hexdigest()[:16]
        print(f"[fingerprint] ✅ Хеш создан: {hash_result}")
        return hash_result
    except Exception as e:
        print(f"[fingerprint] ❌ Ошибка: {type(e).__name__}: {e}")
        return None


def extract_video_frame(video_path: str) -> bytes:
    """
    Извлекает первый кадр из видео и возвращает как байты изображения.
    Требует ffmpeg.
    """
    try:
        import subprocess
        import tempfile
        
        # Создаём временный файл для кадра
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_frame_path = tmp.name
        
        # Извлекаем первый кадр через ffmpeg
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vframes', '1',
            '-f', 'image2',
            '-y',
            tmp_frame_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        
        if result.returncode != 0:
            print("[video_frame] ❌ ffmpeg завершился с ошибкой")
            os.remove(tmp_frame_path)
            return None
        
        # Читаем кадр
        with open(tmp_frame_path, 'rb') as f:
            frame_bytes = f.read()
        
        # Удаляем временный файл
        os.remove(tmp_frame_path)
        
        print(f"[video_frame] ✅ Кадр извлечён из видео")
        return frame_bytes
        
    except Exception as e:
        print(f"[video_frame] ❌ Ошибка: {type(e).__name__}: {e}")
        return None


def hamming_distance(hex1, hex2):
    # убираем префикс media:, если есть
    hex1 = hex1.split(":", 1)[-1] if ":" in hex1 else hex1
    hex2 = hex2.split(":", 1)[-1] if ":" in hex2 else hex2

    b1 = bin(int(hex1, 16))[2:].zfill(len(hex1) * 4)
    b2 = bin(int(hex2, 16))[2:].zfill(len(hex2) * 4)
    return sum(c1 != c2 for c1, c2 in zip(b1, b2))


def is_duplicate(fp: str, seen: dict, max_distance: int = 5) -> bool:
    """
    Проверяет, есть ли похожий fingerprint в seen.
    """
    if not fp:
        return False
    
    for old_fp in seen.keys():
        if hamming_distance(fp, old_fp) <= max_distance:
            return True
    return False


def get_media_fingerprint(media_bytes: bytes = None, file_path: str = None, is_video: bool = False) -> str:
    """
    Универсальный вызов для внешнего кода.
    Для видео нужно передать file_path и is_video=True.
    Для изображений можно передать media_bytes.
    """
    if is_video and file_path:
        # Для видео извлекаем первый кадр
        frame_bytes = extract_video_frame(file_path)
        if not frame_bytes:
            return None
        return quick_fingerprint(frame_bytes)
    elif media_bytes:
        # Для изображений используем байты напрямую
        return quick_fingerprint(media_bytes)
    else:
        print("[fingerprint] ❌ Нужны либо media_bytes, либо file_path с is_video=True")
        return None


def can_fingerprint(file_path: str) -> bool:
    """
    Проверяет, можно ли создать fingerprint для файла
    (только для изображений и видео с превью)
    """
    try:
        Image.open(file_path).verify()
        return True
    except:
        return False
