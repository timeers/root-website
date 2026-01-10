# import re
# import hashlib
# from datetime import datetime
# from django.core.management.base import BaseCommand
# from the_gatehouse.models import Changelog, ChangelogEntry


# CHANGELOG_PATH = "changelog.md"

# CATEGORY_MAP = {
#     "new features": "feature",
#     "improvements": "improvement",
#     "bug fixes": "bugfix",
#     "breaking changes": "breaking",
#     "known issues": "issues",
# }

# def compute_hash(text):
#     normalized = "\n".join(
#         line.rstrip() for line in text.strip().splitlines()
#     )
#     return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# class Command(BaseCommand):
#     help = "Import changelog.md into Changelog and ChangelogEntry models"

#     def add_arguments(self, parser):
#         parser.add_argument(
#             "--dry-run",
#             action="store_true",
#             help="Parse changelog.md without saving anything"
#         )



#     def handle(self, *args, **options):
#         dry_run = options["dry_run"]

#         with open(CHANGELOG_PATH, "r", encoding="utf-8") as f:
#             content = f.read()

#         # Split by version sections
#         # versions = re.split(r"\n## \[", content)[1:]
#         versions = re.split(r"^## \[", content, flags=re.MULTILINE)[1:]


#         for section in versions:
#             section_hash = compute_hash(section)

#             header, _, body = section.partition("\n")

#             lines = body.strip().splitlines()

#             description_lines = []
#             category_start_index = None

#             for i, line in enumerate(lines):
#                 if line.startswith("### "):
#                     category_start_index = i
#                     break
#                 if line.startswith("- "):
#                     description_lines.append(line[2:].strip())

#             description = "\n".join(description_lines)


#             match = re.match(
#                 r"([^\]]+)\] - (\d{4}-\d{1,2}-\d{1,2})\s*(.*)",
#                 header
#             )
#             if not match:
#                 self.stdout.write(self.style.WARNING(
#                     f"Skipping unparseable section header: {header}"
#                 ))
#                 continue

#             version, date_str, title = match.groups()
#             title = title.strip()
#             try:
#                 date = datetime.strptime(date_str, "%Y-%m-%d").date()
#             except ValueError:
#                 self.stdout.write(
#                     self.style.ERROR(f"Invalid date format in version {version}: {date_str}")
#                 )
#                 continue


#             existing = Changelog.objects.filter(version=version).first()

#             if existing:
#                 if existing.source_hash == section_hash:
#                     self.stdout.write(f"Skipping unchanged version {version}")
#                     continue
#                 else:
#                     if dry_run:
#                         self.stdout.write(self.style.WARNING(
#                             f"[DRY RUN] Would update changelog {version}"
#                         ))
#                         continue
#                     else:
#                         self.stdout.write(
#                             self.style.WARNING(f"Updating changed version {version}")
#                         )

#                         if not dry_run:
#                             existing.entries.all().delete()
#                             existing.title = title
#                             existing.date = date
#                             existing.source_hash = section_hash
#                             existing.description = description
#                             existing.save()
#                             changelog = existing
#             else:
#                 if dry_run:
#                     self.stdout.write(self.style.WARNING(
#                         f"[DRY RUN] Would create changelog {version}"
#                     ))
#                     continue
#                 else:
#                     changelog = Changelog.objects.create(
#                         version=version,
#                         title=title,
#                         date=date,
#                         description=description,
#                         source_hash=section_hash,
#                     )
#                     self.stdout.write(self.style.SUCCESS(f"Created changelog {version}"))

#             # Parse categories
#             if category_start_index is None:
#                 category_blocks = []
#             else:
#                 category_body = "\n".join(lines[category_start_index:])
#                 category_blocks = re.split(r"\n### ", category_body)[1:]


#             for block in category_blocks:
#                 lines = block.strip().splitlines()

#                 category_name = lines[0].strip()

#                 category_key = CATEGORY_MAP.get(category_name.lower())
#                 if not category_key:
#                     continue
                
#                 order = 0
#                 for line in lines[1:]:
#                     if line.startswith("- "):
#                         entry_description = line[2:].strip()
#                         if dry_run:
#                             self.stdout.write(
#                                 f" - [{category_key}] {entry_description}"
#                             )
#                         else:
#                             ChangelogEntry.objects.create(
#                                 changelog=changelog,
#                                 category=category_key,
#                                 description=entry_description,
#                                 order=order,
#                             )
#                         order += 1

#             if not dry_run:
#                 self.stdout.write(
#                     self.style.SUCCESS(f"Imported entries for {version}")
#                 )
#             else:
#                 self.stdout.write(self.style.WARNING(
#                     f"[DRY RUN] Finished parsing {version}"
#                 ))
import re
import hashlib
from datetime import datetime
from django.core.management.base import BaseCommand
from the_gatehouse.models import Changelog, ChangelogEntry


CHANGELOG_PATH = "changelog.md"

CATEGORY_MAP = {
    "new features": "feature",
    "improvements": "improvement",
    "bug fixes": "bugfix",
    "breaking changes": "breaking",
    "known issues": "issues",
}

def compute_hash(text):
    normalized = "\n".join(
        line.rstrip() for line in text.strip().splitlines()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Command(BaseCommand):
    help = "Import changelog.md into Changelog and ChangelogEntry models"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse changelog.md without saving anything"
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with open(CHANGELOG_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # Split by version sections
        versions = re.split(r"^## \[", content, flags=re.MULTILINE)[1:]

        for section in versions:
            section_hash = compute_hash(section)
            header, _, body = section.partition("\n")
            lines = body.strip().splitlines()

            # Find where categories start
            category_start_index = None
            for i, line in enumerate(lines):
                if line.startswith("### "):
                    category_start_index = i
                    break

            # Description is everything before the first category
            description_lines = []
            if category_start_index is not None:
                for line in lines[:category_start_index]:
                    stripped = line.strip()
                    # Skip empty lines and bullet points
                    if stripped and not stripped.startswith("- "):
                        description_lines.append(stripped)
            
            description = "\n".join(description_lines)

            # Parse version header
            match = re.match(
                r"([^\]]+)\] - (\d{4}-\d{1,2}-\d{1,2})\s*(.*)",
                header
            )
            if not match:
                self.stdout.write(self.style.WARNING(
                    f"Skipping unparseable section header: {header}"
                ))
                continue

            version, date_str, title = match.groups()
            title = title.strip()
            
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(f"Invalid date format in version {version}: {date_str}")
                )
                continue

            # Check if changelog exists
            existing = Changelog.objects.filter(version=version).first()

            if existing:
                if existing.source_hash == section_hash:
                    self.stdout.write(f"Skipping unchanged version {version}")
                    continue
                else:
                    if dry_run:
                        self.stdout.write(self.style.WARNING(
                            f"[DRY RUN] Would update changelog {version}"
                        ))
                        changelog = existing  # For dry run entry processing
                    else:
                        self.stdout.write(
                            self.style.WARNING(f"Updating changed version {version}")
                        )
                        existing.entries.all().delete()
                        existing.title = title
                        existing.date = date
                        existing.source_hash = section_hash
                        existing.description = description
                        existing.save()
                        changelog = existing
            else:
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        f"[DRY RUN] Would create changelog {version}"
                    ))
                    # Create a dummy changelog for dry run to show entries
                    changelog = type('obj', (object,), {'version': version})()
                else:
                    changelog = Changelog.objects.create(
                        version=version,
                        title=title,
                        date=date,
                        description=description,
                        source_hash=section_hash,
                    )
                    self.stdout.write(self.style.SUCCESS(f"Created changelog {version}"))

            # Parse categories
            if category_start_index is None:
                category_blocks = []
            else:
                category_body = "\n".join(lines[category_start_index:])
                category_blocks = re.split(r"\n### ", category_body)
                
                # Handle the first block (doesn't have \n### prefix)
                if category_blocks:
                    # First block already has ### from the original split point
                    # Remaining blocks need ### added back
                    category_blocks = [category_blocks[0]] + [f"### {block}" for block in category_blocks[1:]]

            for block in category_blocks:
                block_lines = block.strip().splitlines()
                if not block_lines:
                    continue

                # First line is the category name (remove ### if present)
                category_name = block_lines[0].replace("###", "").strip()

                category_key = CATEGORY_MAP.get(category_name.lower())
                if not category_key:
                    self.stdout.write(self.style.WARNING(
                        f"Unknown category in {version}: '{category_name}'"
                    ))
                    continue
                
                order = 0
                for line in block_lines[1:]:
                    if line.startswith("- "):
                        entry_description = line[2:].strip()
                        if dry_run:
                            self.stdout.write(
                                f"  [{category_key}] {entry_description}"
                            )
                        else:
                            ChangelogEntry.objects.create(
                                changelog=changelog,
                                category=category_key,
                                description=entry_description,
                                order=order,
                            )
                        order += 1

            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"Imported entries for {version}")
                )
            else:
                self.stdout.write(self.style.WARNING(
                    f"[DRY RUN] Finished parsing {version}\n"
                ))