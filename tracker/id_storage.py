from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from .id_crypto import decrypt_bytes, encrypt_bytes


class EncryptedIDStorage(FileSystemStorage):
    """FileSystemStorage that encrypts ID files at rest.

    Files on disk are ciphertext; Django reads through this storage and gets
    plaintext back.  A direct web request to the media URL therefore receives
    encrypted bytes, not the ID image.
    """

    def deconstruct(self):
        return ("tracker.id_storage.EncryptedIDStorage", [], {})

    def _save(self, name, content):
        content.seek(0)
        raw = content.read()
        encrypted = encrypt_bytes(raw)
        return super()._save(name, ContentFile(encrypted, name=name))

    def open(self, name, mode="rb"):
        stored = super().open(name, "rb")
        try:
            raw = stored.read()
        finally:
            stored.close()
        return ContentFile(decrypt_bytes(raw, allow_plaintext=True), name=name)
