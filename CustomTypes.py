import base64
from sqlalchemy.types import TypeDecorator, LargeBinary

class ImageBase64(TypeDecorator):
    impl = LargeBinary

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            if value.startswith('data:image'):
                header, encoded = value.split(',', 1)
            else:
                encoded = value
            try:
                return base64.b64decode(encoded)
            except base64.binascii.Error as e:
                print(f"ERROR (CustomType bind): Falha ao decodificar Base64 para binário: {e}")
                return None
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return f"data:image/jpeg;base64,{base64.b64encode(value).decode('utf-8')}"
            except Exception as e:
                print(f"ERROR (CustomType result): Falha ao codificar binário para Base64: {e}")
                return None
        return value

    def copy(self, **kw):
        return ImageBase64(self.impl.length)