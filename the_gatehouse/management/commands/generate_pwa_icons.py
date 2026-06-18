from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image


BRAND_COLOR = (95, 120, 138, 255)  # #5f788a
SOURCE_REL = 'the_keep/static/images/favicon.png'
OUTPUT_DIR_REL = 'the_keep/static/images'


class Command(BaseCommand):
    help = 'Generates PWA icons (192, 512, maskable 512) from the existing favicon.png'

    def handle(self, *args, **kwargs):
        source = Path(settings.BASE_DIR) / SOURCE_REL
        output_dir = Path(settings.BASE_DIR) / OUTPUT_DIR_REL

        if not source.exists():
            raise CommandError(f'Source favicon not found at {source}')

        favicon = Image.open(source).convert('RGBA')

        for size in (192, 512):
            resized = favicon.resize((size, size), Image.LANCZOS)
            out_path = output_dir / f'icon-{size}.png'
            resized.save(out_path, 'PNG')
            self.stdout.write(self.style.SUCCESS(f'Wrote {out_path} ({size}x{size})'))

        maskable = Image.new('RGBA', (512, 512), BRAND_COLOR)
        inner = favicon.resize((410, 410), Image.LANCZOS)
        offset = ((512 - 410) // 2, (512 - 410) // 2)
        maskable.paste(inner, offset, inner)
        maskable_path = output_dir / 'icon-maskable-512.png'
        maskable.save(maskable_path, 'PNG')
        self.stdout.write(self.style.SUCCESS(f'Wrote {maskable_path} (512x512 maskable)'))
