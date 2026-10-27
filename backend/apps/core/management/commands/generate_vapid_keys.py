import base64

from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_vapid_keypair():
    """Returns (private_key, public_key) as the base64url-encoded raw EC P-256 values that
    both pywebpush (server) and the browser Push API (client, applicationServerKey) expect."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    priv_value = private_key.private_numbers().private_value
    pub_numbers = private_key.public_key().public_numbers()
    priv_raw = priv_value.to_bytes(32, "big")
    pub_raw = b"\x04" + pub_numbers.x.to_bytes(32, "big") + pub_numbers.y.to_bytes(32, "big")
    return _b64url(priv_raw), _b64url(pub_raw)


class Command(BaseCommand):
    help = "Generate a VAPID keypair for Web Push. Set the output as ERP_WEBPUSH_PRIVATE_KEY " \
           "(server only) and ERP_WEBPUSH_PUBLIC_KEY (safe to ship to the frontend)."

    def handle(self, *args, **options):
        private_key, public_key = generate_vapid_keypair()
        self.stdout.write(f"ERP_WEBPUSH_PRIVATE_KEY={private_key}")
        self.stdout.write(f"ERP_WEBPUSH_PUBLIC_KEY={public_key}")
