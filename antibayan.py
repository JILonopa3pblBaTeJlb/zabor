# antibayan.py
import io
import os
import numpy as np
from PIL import Image
import hashlib

# ========== Perceptual Hash (pHash) ==========
def dhash(image, hash_size=16):
    """
    Difference Hash - более устойчив к изменениям чем average hash.
    hash_size=16 даёт 256 бит (в 4 раза больше чем было).
    """
    # Изменяем размер
    image = image.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    
    # Конвертируем в оттенки серого
    pixels = np.asarray(image.convert('L'))
    
    # Вычисляем разницу между соседними пикселями
    diff = pixels[:, 1:] > pixels[:, :-1]
    
    return diff.flatten()


def quick_fingerprint(img_bytes: bytes) -> str:
    """
    Возвращает perceptual hash изображения.
    Более устойчив к ресайзу, компрессии, легким изменениям.
    """
    if not img_bytes or len(img_bytes) == 0:
        print("[fingerprint] ❌ Пустые байты")
        return None
    
    try:
        # Открываем изображение
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()
        
        # Открываем заново после verify
        img = Image.open(io.BytesIO(img_bytes))
        
        # Используем difference hash
        hash_bits = dhash(img, hash_size=16)
        
        # Конвертируем в hex
        bitstring = "".join('1' if bit else '0' for bit in hash_bits)
        hash_int = int(bitstring, 2)
        hash_hex = format(hash_int, '064x')  # 256 бит = 64 hex символа
        
        print(f"[fingerprint] ✅ Хеш создан: {hash_hex[:16]}...")
        return hash_hex
        
    except Exception as e:
        print(f"[fingerprint] ❌ Ошибка: {type(e).__name__}: {e}")
        return None


def extract_video_frame(video_path: str, frame_number: int = 5) -> bytes:
    """
    Извлекает кадр из видео (не первый, а например 5-й).
    Первые кадры могут быть чёрными или с лого канала.
    """
    try:
        import subprocess
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_frame_path = tmp.name
        
        # Извлекаем N-й кадр
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'select=eq(n\\,{frame_number})',
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
        
        with open(tmp_frame_path, 'rb') as f:
            frame_bytes = f.read()
        
        os.remove(tmp_frame_path)
        
        print(f"[video_frame] ✅ Кадр {frame_number} извлечён из видео")
        return frame_bytes
        
    except Exception as e:
        print(f"[video_frame] ❌ Ошибка: {type(e).__name__}: {e}")
        return None


def hamming_distance(hex1, hex2):
    """Вычисляет Hamming distance между двумя хешами."""
    if not hex1 or not hex2:
        return float('inf')
    
    # Убираем префиксы если есть
    hex1 = hex1.split(":", 1)[-1] if ":" in hex1 else hex1
    hex2 = hex2.split(":", 1)[-1] if ":" in hex2 else hex2
    
    # Приводим к одной длине
    max_len = max(len(hex1), len(hex2))
    hex1 = hex1.zfill(max_len)
    hex2 = hex2.zfill(max_len)
    
    # Конвертируем в бинарный вид
    b1 = bin(int(hex1, 16))[2:].zfill(len(hex1) * 4)
    b2 = bin(int(hex2, 16))[2:].zfill(len(hex2) * 4)
    
    return sum(c1 != c2 for c1, c2 in zip(b1, b2))


def is_duplicate(fp: str, seen: dict, max_distance: int = 15) -> bool:
    """
    Проверяет, есть ли похожий fingerprint в seen.
    max_distance=15 означает что допускается ~6% различий (15 из 256 бит).
    """
    if not fp:
        return False
    
    for old_fp in seen.keys():
        dist = hamming_distance(fp, old_fp)
        if dist <= max_distance:
            print(f"[bayan] 🔍 Найден похожий контент (расстояние: {dist})")
            return True
    
    return False


def get_media_fingerprint(media_bytes: bytes = None, file_path: str = None, is_video: bool = False) -> str:
    """
    Универсальный вызов для внешнего кода.
    """
    if is_video and file_path:
        # Для видео извлекаем 5-й кадр (пропускаем возможные intro/logo)
        frame_bytes = extract_video_frame(file_path, frame_number=5)
        if not frame_bytes:
            return None
        return quick_fingerprint(frame_bytes)
    elif media_bytes:
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
