"""Offline checks; never saves or transmits source images or biometric data."""
import json
from pathlib import Path
import cv2
import face_recognition
from pyzbar import pyzbar

qr = cv2.QRCodeEncoder_create().encode('DOCKER-TEST-101')
qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
qr = cv2.resize(qr, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
for mirrored in (False, True):
    decoded = pyzbar.decode(cv2.flip(qr, 1) if mirrored else qr)
    print(json.dumps({'check': 'qr', 'mirrored': mirrored,
                      'decoded': [item.data.decode() for item in decoded]}), flush=True)

counts = {'files': 0, 'with_faces': 0, 'without_faces': 0, 'errors': 0}
for path in Path('/app/data/faces').iterdir():
    if path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        continue
    counts['files'] += 1
    try:
        encodings = face_recognition.face_encodings(face_recognition.load_image_file(path))
        counts['with_faces' if encodings else 'without_faces'] += 1
    except Exception as error:
        counts['errors'] += 1
        print(json.dumps({'error_type': type(error).__name__}), flush=True)
print(json.dumps({'check': 'face_enrollment', **counts}), flush=True)
