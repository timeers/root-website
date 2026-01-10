from django.core.management.base import BaseCommand, CommandError
from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import ImageField
from PIL import Image
from io import BytesIO
import os


MODEL_IMAGE_FIELDS = {
    "the_keep.Post": ["board_image", "card_image", "board_2_image", "card_2_image"],
    "the_keep.PostTranslation": ["translated_board_image", "translated_card_image", "translated_board_2_image", "translated_card_2_image"],
    "the_keep.Piece": ["small_icon"],
}


class Command(BaseCommand):
    help = "Convert WebP images to PNG for predefined image fields on supported models"

    def add_arguments(self, parser):
        parser.add_argument(
            "model",
            help="Model in the form app_label.ModelName or just ModelName",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing or deleting files",
        )

    def handle(self, *args, **options):
        model_input = options["model"]
        dry_run = options["dry_run"]

        # Allow passing either "Post" or "the_keep.Post"
        if "." in model_input:
            model_label = model_input
        else:
            model_label = f"the_keep.{model_input}"

        # 1️⃣ Check model is supported
        if model_label not in MODEL_IMAGE_FIELDS:
            self.stdout.write(
                self.style.WARNING(
                    f"Model {model_label} has no registered image fields — skipping"
                )
            )
            return

        try:
            Model = apps.get_model(model_label)
        except LookupError:
            raise CommandError(f"Invalid model: {model_label}")

        field_names = MODEL_IMAGE_FIELDS[model_label]

        # 2️⃣ Validate fields exist and are ImageFields
        for name in field_names:
            try:
                field = Model._meta.get_field(name)
                if not isinstance(field, ImageField):
                    raise CommandError(
                        f"{model_label}.{name} is not an ImageField"
                    )
            except Exception as e:
                raise CommandError(
                    f"Field {name} not found on {model_label}: {e}"
                )

        # 3️⃣ Process objects
        converted_count = 0
        missing_files = 0
        skipped_count = 0
        
        for obj in Model.objects.all():
            for field_name in field_names:
                image_field = getattr(obj, field_name)

                if not image_field:
                    continue

                old_name = image_field.name

                if not image_field.name.lower().endswith(".webp"):
                    skipped_count += 1
                    if not image_field.name.lower().endswith(".png"):
                        self.stderr.write(
                            self.style.WARNING(
                                f"SKIPPING FILE: {model_label} {obj}({obj.pk}) | "
                                f"{field_name}: {old_name} (file not .webp format)"
                            )
                        )
                    continue

                # Check if file actually exists
                if not default_storage.exists(old_name):
                    missing_files += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"✗ MISSING FILE: {model_label} {obj}({obj.pk}) | "
                            f"{field_name}: {old_name} (file not found in storage)"
                        )
                    )
                    continue

                try:
                    if dry_run:
                        # Try to open the file to verify it's readable
                        try:
                            with default_storage.open(old_name, "rb") as f:
                                img = Image.open(f)
                                img.verify()  # Verify it's a valid image
                            
                            base_name = os.path.splitext(os.path.basename(old_name))[0]
                            new_filename = f"{base_name}.png"
                            
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"✓ [DRY RUN] {model_label} {obj}({obj.pk}) | "
                                    f"{field_name}: {old_name} → {new_filename}"
                                )
                            )
                            converted_count += 1
                        except Exception as e:
                            self.stderr.write(
                                self.style.ERROR(
                                    f"✗ CORRUPT FILE: {model_label} {obj}({obj.pk}) | "
                                    f"{field_name}: {old_name} (cannot open: {e})"
                                )
                            )
                            missing_files += 1
                        continue

                    self.stdout.write(
                        f"Converting {model_label} {obj.pk} | {field_name}: {old_name}"
                    )

                    # Open old file
                    with default_storage.open(old_name, "rb") as f:
                        img = Image.open(f).convert("RGBA")

                    # Convert to PNG
                    buffer = BytesIO()
                    img.save(buffer, format="PNG", optimize=True)
                    buffer.seek(0)

                    # Generate new filename (preserve original name structure)
                    base_name = os.path.splitext(os.path.basename(old_name))[0]
                    new_filename = f"{base_name}.png"

                    # Save new file (upload_to() is respected here)
                    image_field.save(
                        new_filename,
                        ContentFile(buffer.read()),
                        save=False,
                    )

                    obj.save(update_fields=[field_name])

                    # Verify new file exists before deleting old one
                    if default_storage.exists(image_field.name):
                        default_storage.delete(old_name)
                        converted_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(f"  ✓ Converted to {image_field.name}")
                        )
                    else:
                        self.stderr.write(
                            self.style.ERROR(
                                f"  ✗ New file not found, kept original"
                            )
                        )

                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Failed {model_label} {obj}({obj.pk}) | "
                            f"{field_name}: {exc}"
                        )
                    )

        # Summary
        self.stdout.write("\n" + "="*60)
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete — Summary:"
                )
            )
            self.stdout.write(f"  • Would convert: {converted_count} images")
            if missing_files > 0:
                self.stdout.write(
                    self.style.ERROR(f"  • Missing/corrupt files: {missing_files}")
                )
            if skipped_count > 0:
                self.stdout.write(f"  • Skipped (not .webp): {skipped_count}")
            
            if missing_files > 0:
                self.stdout.write(
                    self.style.WARNING(
                        "\n Warning: Some files are missing or corrupt. "
                        "Fix these before running without --dry-run"
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Image conversion complete — converted {converted_count} images"
                )
            )
            if missing_files > 0:
                self.stdout.write(
                    self.style.WARNING(f"  • Skipped due to missing files: {missing_files}")
                )