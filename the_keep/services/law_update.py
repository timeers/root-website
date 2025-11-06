import yaml
import re
from the_keep.models import Faction, Law, LawGroup
from the_keep.utils import normalize_name, replace_placeholders, DEFAULT_TITLES_TRANSLATIONS
from django.db import transaction

# Functions for converting Law of Root yaml file into Law objects

# maping from seyria to rdb {{ }} format
REFERENCE_NAME_MAP = {
    'whenhired': 'hired',
    'ability': 'ability',
    'daylight': 'daylight',
    'birdsong': 'birdsong',
    'marquise': 'cat',
    'eyrie': 'bird',
    'woodland': 'bunny',
    'vagabond': 'vb',
    'cult': 'lizard',
    'riverfolk': 'otter',
    'duchy': 'mole',
    'corvid': 'crow',
    'warlord': 'rat',
    'keepers': 'badger',
    'diaspora': 'frog',
    'council': 'bat',
    'knaves': 'skunk',
}


def get_translated_title(key, target_lang_code):
    translations = DEFAULT_TITLES_TRANSLATIONS.get(key)
    if translations:
        return translations.get(target_lang_code, key)  # fallback to English key
    return key



class NoPrimeLawError(Exception):
    pass

def serialize_group(prime_law, include_id=True):
    if not prime_law:
        raise NoPrimeLawError("No prime law found.")

    laws = Law.objects.filter(group=prime_law.group, language=prime_law.language).select_related('parent').prefetch_related('children')
    
    law_color = prime_law.group.post.color if prime_law.group.post else '#000000'

    # Base output
    base_entry = {
        'name': prime_law.title.strip(),
        'color': law_color,
        'children': []
    }

    # Only add pretext if description exists
    if prime_law.description:
        text = replace_placeholders(prime_law.description.strip())
        references = ''
        if prime_law.reference_laws:
            for reference in prime_law.reference_laws.all():
                if reference.group.type == "Official":
                    references += f"(`rule:{reference.get_law_index()}`)"
                else:
                    references += f"({reference})"
        base_entry['pretext'] = text + references

    # Only include ID if requested
    if include_id:
        base_entry['id'] = prime_law.id

    output = [base_entry]

    top_level_laws = laws.filter(parent__isnull=True, prime_law=False).order_by('position')
    for law in top_level_laws:
        output[0]['children'].append(serialize_law(law, include_id=include_id))
    return output



def serialize_law(law, include_id=True):
    entry = {
        'name': replace_placeholders(law.title.strip())
    }

    if law.plain_title and law.plain_title.strip() != law.title.strip():
        entry['plainName'] = replace_placeholders(law.plain_title.strip())

    if law.description:
        text = replace_placeholders(law.description.strip())

        references = ''
        if law.reference_laws:
            for reference in law.reference_laws.all():
                if reference.group.type == "Official":
                    references += f"(`rule:{reference.get_law_index()}`)"
                else:
                    references += f"({reference})"
        if law.level == 0:
            entry['pretext'] = text + references
        else:
            entry['text'] = text + references

    # Conditionally include ID
    if include_id:
        entry['id'] = law.id

    children = law.children.all().order_by('position')
    if children.exists():

        entry['children'] = [serialize_law(child, include_id=include_id) for child in children]


    return entry

# comparing an uploaded yaml law file with the current law
def compare_structure_strict(generated, uploaded, path="'Law'"):
    mismatches = []

    if type(generated) != type(uploaded):
        mismatches.append(f"Type mismatch at {path}: {type(generated).__name__} vs {type(uploaded).__name__}")
        return mismatches

    if isinstance(generated, list):
        if len(generated) != len(uploaded):
            mismatches.append(f"Length mismatch at {path}: {len(generated)} vs {len(uploaded)}")

        gen_names = [
            (normalize_name(item.get('name', '')), item.get('name', ''))
            if isinstance(item, dict) else ('', '')
            for item in generated
        ]

        up_names = [
            (normalize_name(item.get('name', '')), item.get('name', ''))
            if isinstance(item, dict) else ('', '')
            for item in uploaded
        ]

        # Extract normalized names for easier logic
        gen_norms = [n[0] for n in gen_names]
        up_norms = [n[0] for n in up_names]

        # Check for missing/extra items using normalized names
        for norm, orig in gen_names:
            if norm and norm not in up_norms:
                mismatches.append(f"Missing expected item '{orig}' in uploaded at {path}")

        for norm, orig in up_names:
            if norm and norm not in gen_norms:
                mismatches.append(f"Unexpected item '{orig}' found in uploaded at {path}")


        for i, (gen_item, up_item) in enumerate(zip(generated, uploaded)):
            expected_norm, expected_orig = gen_names[i]
            actual_norm, actual_orig = up_names[i]

            new_path = f"{path} > '{expected_orig or '<unnamed>'}'"

            if expected_norm != actual_norm:
                if actual_norm in gen_norms:
                    correct_index = gen_norms.index(actual_norm)
                    mismatches.append(
                        f"Out of order Law at {new_path}: expected '{expected_orig}', got '{actual_orig}' "
                        f"(found at index {correct_index})"
                    )
                else:
                    mismatches.append(
                        f"Law Name mismatch at {new_path}: new name '{actual_orig}'"
                    )

            mismatches += compare_structure_strict(gen_item, up_item, new_path)

    elif isinstance(generated, dict):
        gen_child = generated.get('children', [])
        up_child = uploaded.get('children', [])
        if gen_child or up_child:
            child_path = f"{path}"
            mismatches += compare_structure_strict(gen_child, up_child, child_path)


    return mismatches





def update_laws_by_structure(generated_data, uploaded_data, lang_code):
    @transaction.atomic
    def recursive_update(generated, uploaded):
        for gen_item, up_item in zip(generated, uploaded):
            law_id = gen_item.get("id")
            if law_id is None:
                continue  # Skip if no ID found
            try:
                law = Law.objects.get(id=law_id)
            except Law.DoesNotExist:
                continue

            # Use uploaded text or pretext to update description
            new_desc = up_item.get("text") or up_item.get("pretext")
            new_title, _ = replace_special_references(up_item.get("name"), lang_code)
            reference_laws = None
            law.title = new_title
            if new_desc:
                description, reference_laws = replace_special_references(new_desc.strip(), lang_code)
                law.description = description
            law.save()
            if reference_laws:
                law.reference_laws.set(reference_laws)  # This replaces all existing references
            else:
                law.reference_laws.clear()  # If no references found, clear the field

            # Recurse through children
            gen_children = gen_item.get("children", [])
            up_children = up_item.get("children", [])
            if gen_children or up_children:
                recursive_update(gen_children, up_children)


    # Run the update inside an atomic block
    recursive_update(generated_data, uploaded_data)



def replace_special_references(text, lang_code):
    reference_laws = set()

    def find_law_by_rule_index(rule_index_str, lang_code, second_pass=False):

        try:
            group_idx_str, law_idx_str = rule_index_str.split('.', 1)
            group_idx = int(group_idx_str)

            if law_idx_str == "0":
                law_idx_str = ""

        except ValueError:
            return None

        all_groups = list(LawGroup.objects.filter(laws__language__code=lang_code).distinct())
        if group_idx - 1 >= len(all_groups) or group_idx <= 0:
            return None

        group = all_groups[group_idx - 1]
        law = Law.objects.filter(group=group, law_index=law_idx_str, language__code=lang_code).first()

        return law

    def backtick_replacer(match):
        content = match.group(1)

        # Handle rule: still if present and not already matched in previous pass
        if content.startswith("rule:"):
            rule_content = content[5:]
            law = find_law_by_rule_index(rule_content, lang_code)
            if law:
                rule_content=law.law_code
                reference_laws.add(law)
                return f"{rule_content}"  # Remove `rule:x`
            else:
                return f"{rule_content}"

        # map faction references
        if content.startswith("faction:"):
            # Extract the faction key between ':' and '$' (or to the end if no '$')
            faction_part = content[len("faction:"):]
            if '$' in faction_part:
                key = faction_part.split('$', 1)[0]
            else:
                key = faction_part

            if key in REFERENCE_NAME_MAP:
                return f"{{{{{REFERENCE_NAME_MAP[key]}}}}}"


        # map hireling references
        elif content.startswith("hireling:"):
            parts = content.split(":")
            if len(parts) == 2:
                key = parts[1]
                if key in REFERENCE_NAME_MAP:
                    return f"{{{{{REFERENCE_NAME_MAP[key]}}}}}"

        # Replace `item:x` with {{x}}
        elif content.startswith("item:"):
            parts = content.split(":")
            if len(parts) == 2:
                key = parts[1]
                return f"{{{{{key}}}}}"

        return match.group(0)  # leave unchanged

    # Handle `...` references
    text = re.sub(r'`([^`]+)`', backtick_replacer, text)

    # Remove unnecessary backslashes before parentheses
    text = re.sub(r'\\\(', '(', text)
    text = re.sub(r'\\\)', ')', text)

    return text, list(reference_laws)

def create_laws_from_yaml(group, language, yaml_data):
    lang_code = language.code

    def create_law(entry, lang_code, parent=None, position=0, is_prime=False):
        # Prefer 'pretext' if present, otherwise use 'text'
        raw_description = entry.get('pretext') or entry.get('text', '')
        if raw_description:
            description, reference_laws = replace_special_references(raw_description, lang_code=lang_code)
        else:
            description, reference_laws = '', []        
        raw_title = entry['name']
        title, _ = replace_special_references(raw_title, lang_code=lang_code)
        if parent and parent.prime_law:
            parent=None

        law = Law.objects.create(
            title=title,
            group=group,
            language=language,
            parent=parent,
            position=position,
            prime_law=is_prime,
            description=description
        )
        if reference_laws:
            law.reference_laws.set(reference_laws)  # This replaces all existing references
        else:
            law.reference_laws.clear()
        # Handle 'children'
        for i, child in enumerate(entry.get('children', [])):
            create_law(child, lang_code, parent=law, position=i)


    for i, entry in enumerate(yaml_data):
        is_prime = i == 0  # Treat first item as prime law
        create_law(entry, lang_code, parent=None, position=i, is_prime=is_prime)



def load_uploaded_yaml(uploaded_file):
    """Parses the uploaded YAML safely and normalizes it."""
    data = yaml.safe_load(uploaded_file)
    if isinstance(data, list):
        return {entry['name']: entry for entry in data}
    elif not isinstance(data, dict):
        raise ValueError("YAML must be a list or dictionary of law groups.")
    return data


def categorize_groups(uploaded_yaml):

    """Splits YAML data into matched, unmatched, and appendix groups."""
    matched, unmatched, appendix = {}, {}, {}
    for idx, (title, group_data) in enumerate(uploaded_yaml.items(), start=1):
        if 'appendix' in group_data:
            appendix[title] = {'data': group_data, 'post': None, 'index': idx}
        elif group_data['color'] == '#000000':
            unmatched[title] = {'data': group_data, 'post': None, 'index': idx}
        else:
            post = Faction.objects.filter(title=title, official=True).first()
            if not post:
                law_group_by_index = LawGroup.objects.filter(abbreviation=idx).first()
                if law_group_by_index:
                    post = Faction.objects.filter(title=law_group_by_index.post.title).first()
            if post:
                matched[title] = {'data': group_data, 'post': post, 'index': idx}
            else:
                unmatched[title] = {'data': group_data, 'post': None, 'index': idx}
    return matched, unmatched, appendix


def process_group(group_title, content, lawgroup_qs, language, group_type, messages=None):

    """Processes an individual group: creates or updates its laws."""
    created = False
    mismatches = []
    group_data = content['data']
    index = content.get('index')
    post = content.get('post')

    group = None
    if group_type == 'Official':
        if post:
            group = lawgroup_qs.filter(post=post).first()
            if not group:
                group = LawGroup.objects.create(post=post, type="Official", abbreviation=index)
        else:
            group = lawgroup_qs.filter(title=group_title).first() or \
                    lawgroup_qs.filter(abbreviation=index).first()
            if not group:
                group = LawGroup.objects.create(title=group_title, abbreviation=index, type="Official")
    elif group_type == 'Appendix':
        group = lawgroup_qs.filter(title=group_title).first() or \
                lawgroup_qs.filter(abbreviation=group_data.get('appendix')).first()
        if not group:
            group = LawGroup.objects.create(
                title=group_title, abbreviation=group_data.get('appendix'), type="Appendix"
            )

    if not group:
        return {"error": f"Could not create or find group for '{group_title}'"}

    prime_law = Law.objects.filter(group=group, language=language, prime_law=True).first()
    if not prime_law:
        create_laws_from_yaml(group, language, [group_data])
        created = True
        prime_law = Law.objects.filter(group=group, language=language, prime_law=True).first()

    generated_yaml = serialize_group(prime_law)
    if not created:
        mismatches = compare_structure_strict(generated_yaml, [group_data])

    if mismatches:
        # if messages:
            # messages.warning(messages, f"Structure mismatch in '{group_title}': {mismatches}")
        return {"mismatch": mismatches}

    return {"update_data": (generated_yaml, [group_data], language.code, group_title), "created": created}


@transaction.atomic
def update_laws_from_yaml(uploaded_yaml, language, messages=None):

    """Main orchestrator that updates all laws for a given language."""
    matched, unmatched, appendix = categorize_groups(uploaded_yaml)

    created_laws, updated_laws, mismatch_laws, error_laws = [], [], [], []
    all_mismatches, laws_to_update = {}, []

    lawgroups_with_post = LawGroup.objects.filter(type='Official', post__isnull=False)
    lawgroups_without_post = LawGroup.objects.filter(type='Official', post__isnull=True)
    lawgroups_appendix = LawGroup.objects.filter(type='Appendix', post__isnull=True)

    # Process all three categories
    for collection, queryset, group_type in [
        (unmatched, lawgroups_without_post, 'Official'),
        (matched, lawgroups_with_post, 'Official'),
        (appendix, lawgroups_appendix, 'Appendix'),
        ]:
        for group_title, content in collection.items():
            result = process_group(group_title, content, queryset, language, group_type, messages)
            
            if "mismatch" in result:
                mismatch_laws.append(group_title)
                all_mismatches[group_title] = result["mismatch"]
            elif "error" in result:
                error_laws.append(group_title)
            elif "update_data" in result:
                if result.get("created"):
                    created_laws.append(group_title)
                else:
                    updated_laws.append(group_title)
                laws_to_update.append(result["update_data"])
    
    # Apply updates if no mismatches
    if not all_mismatches and not 'error' in result:
        for generated_yaml, group_data, lang_code, group_title in laws_to_update:
            update_laws_by_structure(generated_yaml, group_data, lang_code)
    return created_laws, updated_laws, mismatch_laws, error_laws, all_mismatches
