"""Bounded CPU work, background initialization, persistent face templates."""
import hashlib
import io
import json
import logging
from pathlib import Path
import threading
log = logging.getLogger(__name__)

class Recognizer:
    def __init__(self, faces_dir, cache_path):
        self.faces_dir, self.cache_path = Path(faces_dir), Path(cache_path)
        self.lock, self.info_lock = threading.Lock(), threading.Lock()
        self.info = {'state': 'loading', 'loaded': 0, 'total': 0, 'skipped': 0}
        self.names, self.encodings = [], []

    def status(self):
        with self.info_lock:
            return dict(self.info)

    def update(self, **values):
        with self.info_lock:
            self.info.update(values)

    def start(self):
        threading.Thread(target=self.load, name='face-loader', daemon=True).start()

    @staticmethod
    def image(source, max_pixels=60_000_000):
        from PIL import Image, ImageOps
        import numpy as np
        with Image.open(source) as original:
            if original.width * original.height > max_pixels:
                raise ValueError('Изображение имеет слишком большое разрешение.')
            original.draft('RGB', (800, 800))
            original.thumbnail((800, 800))
            return np.ascontiguousarray(ImageOps.exif_transpose(original).convert('RGB'))

    def load(self):
        try:
            import face_recognition
            self.face = face_recognition
            files = sorted(p for p in self.faces_dir.glob('*') if p.suffix.lower() in ('.jpg', '.jpeg', '.png'))
            self.update(total=len(files))
            cache = {}
            try:
                cache = json.loads(self.cache_path.read_text())
            except (OSError, ValueError):
                pass
            new_cache = {}
            for path in files:
                try:
                    if path.stat().st_size > 30_000_000:
                        raise ValueError('Reference image exceeds 30 MB')
                    with path.open('rb') as source:
                        digest = hashlib.file_digest(source, 'sha256').hexdigest()
                    key = 'v1-800-hog-' + digest
                    cached = cache.get(key)
                    if cached is not None and len(cached) == 128:
                        encoding = cached
                    else:
                        image = self.image(path)
                        locations = self.face.face_locations(image, number_of_times_to_upsample=0, model='hog')
                        if len(locations) != 1:
                            raise ValueError('Reference must contain exactly one face')
                        encoding = self.face.face_encodings(image, locations, num_jitters=1)[0].tolist()
                    self.encodings.append(encoding)
                    self.names.append(path.stem)
                    new_cache[key] = encoding
                    self.update(loaded=len(self.names))
                except Exception:
                    log.warning('Reference image skipped: %s', path.name, exc_info=True)
                    self.update(skipped=self.status()['skipped'] + 1)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(new_cache))
            temporary.replace(self.cache_path)
            self.update(state='ready' if self.names else 'empty')
        except Exception:
            log.exception('Face library initialization failed')
            self.update(state='error')

    def scan(self, data):
        if self.status()['state'] != 'ready':
            raise RuntimeError('База лиц ещё не готова. Проверьте состояние сервера.')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Сервер занят. Повторите через секунду.')
        try:
            from PIL import Image, UnidentifiedImageError
            from pyzbar import pyzbar
            import numpy as np
            try:
                image = self.image(io.BytesIO(data), max_pixels=1_600_000)
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
                raise ValueError('Некорректный кадр или слишком большое разрешение.') from error
            decoded = pyzbar.decode(image, symbols=[pyzbar.ZBarSymbol.QRCODE])
            cabinets = {item.data.decode('utf-8', errors='replace').strip() for item in decoded}
            cabinets = {v for v in cabinets if 0 < len(v) <= 200 and '\ufffd' not in v and not any(ord(c) < 32 for c in v)}
            locations = self.face.face_locations(image, number_of_times_to_upsample=0, model='hog')
            result = {'person': None, 'cabinet': next(iter(cabinets)) if len(cabinets) == 1 else None,
                      'message': 'Покажите лицо и один QR-код кабинета одновременно.'}
            if len(locations) != 1:
                result['message'] = 'В кадре должно быть одно лицо.'
                return result
            encoding = self.face.face_encodings(image, locations, num_jitters=1)[0]
            distances = self.face.face_distance(np.asarray(self.encodings), encoding)
            index = int(np.argmin(distances))
            if distances[index] <= 0.5:
                result['person'] = self.names[index]
            else:
                result['message'] = 'Лицо не распознано.'
            return result
        finally:
            self.lock.release()
