import requests
import base64
from dateutil import parser as dateparser

from django.utils import timezone 
from django.core.files.base import ContentFile
from the_keep.models import RulesFile


# Functions for monitoring the LederCards 'rules' GitHub and downloading changes

OWNER = "LederCards"
REPO = "rules"
BASE_PATH = "content/rules/root"
GITHUB_API_URL = "https://api.github.com"


def github_api(path, token=None):
    """
    Wrapper to fetch file/folder data from GitHub with timeout and error handling.
    If token is provided, uses it for higher rate limits.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{GITHUB_API_URL}/repos/{OWNER}/{REPO}/contents/{path}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        status = response.status_code
        if status == 404:
            # File or folder not found
            print(f"Warning: Resource not found at path: {path}")
            return None
        elif status == 403:
            # Rate limit or forbidden
            print("Error: Access forbidden or rate limited by GitHub API.")
            raise
        else:
            print(f"HTTP error occurred: {http_err}")
            raise
    except requests.exceptions.Timeout:
        print("Error: Request timed out while accessing GitHub API.")
        raise
    except requests.exceptions.RequestException as err:
        print(f"Error: An unexpected error occurred: {err}")
        raise

def get_rules_file_info(folder_path, token=None):
    """
    Fetches the list of files inside a version folder,
    returns the metadata dict for 'rules.yaml' or 'rules.yml', or None if neither exists.
    """
    contents = github_api(folder_path, token)
    if contents is None:
        return None

    for item in contents:
        if item["type"] == "file" and item["name"] in ("rules.yaml", "rules.yml"):
            return item

    return None


def sync_github_rules(language=None, token=None):
    """
    Fetches the rules.yml file from GitHub and stores a new RulesFile (based on SHA).
    Checks all version folders for the highest folder. Selects the rules.yml file and compares SHA and version.
    If the file does not match then a new RulesFile is saved.
    """
    if language is None:
        print('No language selected')
        return False

    locale = language.locale
    api_path = f'{BASE_PATH}/{locale}'
    folders = github_api(api_path, token)
    if folders is None:
        print("No folders found at base path.")
        return False

    newest_folder, newest_version = find_newest_folder_by_name(folders)

    if newest_folder:

        if newest_folder.get("type") != "dir":
            print(f"Folder '{newest_version}' type error.")
            return False

        rules_file_info = get_rules_file_info(newest_folder["path"], token)

        if rules_file_info is None:
            print(f"No rules.yaml or rules.yml found for version '{newest_version}'")
            return False

        sha = rules_file_info["sha"]

        if RulesFile.objects.filter(version=newest_version, sha=sha, language=language).exists():
            print(f"The current version {newest_version} is up to date.")
            return False # no update needed

        # Now fetch the actual content of the rules file
        rules_data = github_api(rules_file_info["path"], token)
        if rules_data is None:
            print(f"Failed to fetch rules content for {newest_version}")
            return False

        commit_date = get_latest_commit_date(rules_file_info["path"], token)

        # Test API without saving the law
        # print(f"Found new rules file for version '{version}':")
        # print(f"  Filename: {rules_file_info['name']}")
        # print(f"  SHA: {sha}")
        # print(f"  Size: {rules_file_info['size']} bytes")
        # # Optionally print first few lines of content for sanity check
        # content = base64.b64decode(rules_data["content"]).decode('utf-8', errors='ignore')
        # preview = '\n'.join(content.splitlines()[:5])
        # print(f"  Preview:\n{preview}\n")

        content = base64.b64decode(rules_data["content"])

        rules_file = RulesFile(
            version=newest_version,
            sha=sha,
            commit_date=commit_date,
            status=RulesFile.Status.NEW,
            language=language,
        )
        rules_file.file.save(f"{newest_version}_{sha[:7]}.yaml", ContentFile(content), save=True)

        print(f"New {locale} rules saved for version '{newest_version}'.")
        return True

    else:
        print("No valid folders found.")
        return False





def get_latest_commit_date(file_path, token=None):
    commits_url = f"https://api.github.com/repos/{OWNER}/{REPO}/commits"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    params = {"path": file_path, "per_page": 1}
    response = requests.get(commits_url, headers=headers, params=params)

    if response.status_code == 200 and response.json():
        commit_date_str = response.json()[0]["commit"]["committer"]["date"]
        return dateparser.isoparse(commit_date_str)
    else:
        print(f"Failed to fetch commit date for path: {file_path}")
        return timezone.now()  # fallback


   
def extract_version_number(folder_name):
    # Assumes folder names start with 'p' followed by an integer
    try:
        return int(folder_name.lstrip('p'))
    except ValueError:
        return -1 

def find_newest_folder_by_name(folders):
    newest_folder = None
    highest_version = -1

    for folder in folders:
        if folder.get("type") != "dir":
            continue

        version_num = extract_version_number(folder["name"])
        if version_num > highest_version:
            highest_version = version_num
            newest_folder = folder

    return newest_folder, highest_version