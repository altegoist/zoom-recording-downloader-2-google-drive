#!/usr/bin/env python3

# Program Name: zoom-recording-downloader.py
# Description:  Zoom Recording Downloader is Google Collab Python script
#               that uses Zoom's API (v2) to download and organize all
#               cloud recordings from a Zoom account onto Google Drive storage.
#               This Python script uses the OAuth method of accessing the Zoom API
# Created:      2020-04-26
# Author:       Ricardo Rodrigues
# Fork by:      Valentin Vasilevskiy
# Updated:      2025-01-18
# Website:      https://github.com/ricardorodrigues-ca/zoom-recording-downloader-2-google-drive
# Forked from:  https://github.com/ricardorodrigues-ca/zoom-recording-downloader

# system libraries
import base64
import datetime
import json
import os
import re as regex
import signal
import sys as system

from google.colab import drive

drive.mount('/content/drive')


# installed libraries
import dateutil.parser as parser
import pathvalidate as path_validate
import requests
import tqdm as progress_bar
from zoneinfo import ZoneInfo

class Color:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    DARK_CYAN = "\033[36m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"

CONF_PATH = "drive/MyDrive/Zoom Recordings/zoom-recording-downloader.conf"
with open(CONF_PATH, encoding="utf-8-sig") as json_file:
    CONF = json.loads(json_file.read())

def config(section, key, default=''):
    try:
        return CONF[section][key]
    except KeyError:
        if default == LookupError:
            print(f"{Color.RED}### No value provided for {section}:{key} in {CONF_PATH}")
            system.exit(1)
        else:
            return default

ACCOUNT_ID = config("OAuth", "account_id", LookupError)
CLIENT_ID = config("OAuth", "client_id", LookupError)
CLIENT_SECRET = config("OAuth", "client_secret", LookupError)

APP_VERSION = "3.2 (OAuth)"

API_ENDPOINT_USER_LIST = "https://api.zoom.us/v2/users"

RECORDING_START_YEAR = config("Recordings", "start_year", datetime.date.today().year)
RECORDING_START_MONTH = config("Recordings", "start_month", 1)
RECORDING_START_DAY = config("Recordings", "start_day", 1)
RECORDING_START_DATE = parser.parse(config("Recordings", "start_date", f"{RECORDING_START_YEAR}-{RECORDING_START_MONTH}-{RECORDING_START_DAY}"))
RECORDING_END_DATE = parser.parse(config("Recordings", "end_date", str(datetime.date.today())))
DOWNLOAD_DIRECTORY = "drive/MyDrive/Zoom Recordings/2025"
COMPLETED_MEETING_IDS_LOG = "drive/MyDrive/Zoom Recordings/completed-downloads.log"
COMPLETED_MEETING_IDS = set()

MEETING_TIMEZONE = ZoneInfo(config("Recordings", "timezone", 'UTC'))
MEETING_STRFTIME = config("Recordings", "strftime", '%Y.%m.%d - %I.%M %p UTC')
MEETING_FILENAME = config("Recordings", "filename", '{meeting_time} - {topic} - {rec_type} - {recording_id}.{file_extension}')
MEETING_FOLDER = config("Recordings", "folder", '{meeting_time} - {topic}')


def load_access_token():
    """ OAuth function, thanks to https://github.com/freelimiter
    """
    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ACCOUNT_ID}"

    client_cred = f"{CLIENT_ID}:{CLIENT_SECRET}"
    client_cred_base64_string = base64.b64encode(client_cred.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {client_cred_base64_string}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = json.loads(requests.request("POST", url, headers=headers).text)

    global ACCESS_TOKEN
    global AUTHORIZATION_HEADER

    try:
        ACCESS_TOKEN = response["access_token"]
        AUTHORIZATION_HEADER = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

    except KeyError:
        print(f"{Color.RED}### The key 'access_token' wasn't found.{Color.END}")


def get_users():
    """ loop through pages and return all users
    """
    response = requests.get(url=API_ENDPOINT_USER_LIST, headers=AUTHORIZATION_HEADER)

    if not response.ok:
        print(response)
        print(
            f"{Color.RED}### Could not retrieve users. Please make sure that your access "
            f"token is still valid{Color.END}"
        )

        system.exit(1)

    page_data = response.json()
    total_pages = int(page_data["page_count"]) + 1

    all_users = []

    for page in range(1, total_pages):
        url = f"{API_ENDPOINT_USER_LIST}?page_number={str(page)}"
        user_data = requests.get(url=url, headers=AUTHORIZATION_HEADER).json()
        users = ([
            (
                user["email"],
                user["id"],
                user["first_name"],
                user["last_name"]
            )
            for user in user_data["users"]
        ])

        all_users.extend(users)
        page += 1

    return all_users


def format_filename(params):
    file_extension = params["file_extension"].lower()
    recording = params["recording"]
    recording_id = params["recording_id"]
    recording_type = params["recording_type"]

    invalid_chars_pattern = r'[<>:"/\\|?*\x00-\x1F]'
    topic = regex.sub(invalid_chars_pattern, '', recording["topic"])
    rec_type = recording_type.replace("_", " ").title()
    meeting_time_utc = parser.parse(recording["start_time"]).replace(tzinfo=datetime.timezone.utc)
    meeting_time_local = meeting_time_utc.astimezone(MEETING_TIMEZONE)
    year = meeting_time_local.strftime("%Y")
    month = meeting_time_local.strftime("%m")
    day = meeting_time_local.strftime("%d")
    meeting_time = meeting_time_local.strftime(MEETING_STRFTIME)

    filename = MEETING_FILENAME.format(**locals())
    folder = MEETING_FOLDER.format(**locals())
    return (filename, folder)


def get_downloads(recording):
    if not recording.get("recording_files"):
        raise Exception

    #print(f"==> All recording files: {json.dumps(recording.get('recording_files', []), indent=4)}")

    downloads = []
    for download in recording["recording_files"]:
        file_type = download["file_type"]
        file_extension = download["file_extension"]
        recording_id = download["id"]

        if file_type == "":
            recording_type = "incomplete"
        elif file_type != "TIMELINE":
            recording_type = download["recording_type"]
        else:
            recording_type = download["file_type"]

        # must append access token to download_url
        download_url = f"{download['download_url']}?access_token={ACCESS_TOKEN}"
        downloads.append((file_type, file_extension, download_url, recording_type, recording_id))

    return downloads


def get_recordings(email, page_size, rec_start_date, rec_end_date):
    return {
        "userId": email,
        "page_size": page_size,
        "from": rec_start_date,
        "to": rec_end_date
    }

def delete_recording(meeting_id):
  """Deletes the Zoom meeting recording by meeting identifier (meeting_id)."""
  delete_url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
  response = requests.delete(url=delete_url, headers=AUTHORIZATION_HEADER)

  if response.status_code == 204:
    print(f"{Color.GREEN}==> Recording with ID {meeting_id} was successfully deleted from Zoom.{Color.END}")
    return True
  else:
    print(f"{Color.RED}### Error deleting recording with ID {meeting_id}: {response.text}{Color.END}")
    return False


def per_delta(start, end, delta):
    """ Generator used to create deltas for recording start and end dates
    """
    curr = start
    while curr < end:
        yield curr, min(curr + delta, end)
        curr += delta


def list_recordings(email):
    """ Start date now split into YEAR, MONTH, and DAY variables (Within 6 month range)
        then get recordings within that range
    """
    recordings = []

    for start, end in per_delta(
        RECORDING_START_DATE,
        RECORDING_END_DATE,
        datetime.timedelta(days=30)
    ):
        post_data = get_recordings(email, 300, start, end)
        response = requests.get(
          url=f"https://api.zoom.us/v2/users/{email}/recordings",
          headers=AUTHORIZATION_HEADER,
          params={**post_data, "include_fields": "download_access_token"}
        )
        #print(f"==> Full API response: {json.dumps(response.json(), indent=4)}")

        recordings_data = response.json()
        recordings.extend(recordings_data["meetings"])

    return recordings


def download_recording(download_url, email, filename, folder_name):
  dl_dir = os.sep.join([DOWNLOAD_DIRECTORY, folder_name])
  sanitized_download_dir = path_validate.sanitize_filepath(dl_dir)
  sanitized_filename = path_validate.sanitize_filename(filename)
  full_filename = os.sep.join([sanitized_download_dir, sanitized_filename])

  os.makedirs(sanitized_download_dir, exist_ok=True)

  response = requests.get(download_url, stream=True)

  # Total size in bytes from Zoom API
  total_size = int(response.headers.get("content-length", 0))
  block_size = 32 * 1024  # 32 KiB block size for download

  print(f"==> Downloading to folder: {folder_name}")
  print(f"==> Filename: {filename}")

  # Create progress bar
  prog_bar = progress_bar.tqdm(total=total_size, unit="iB", unit_scale=True)

  try:
    with open(full_filename, "wb") as fd:
      for chunk in response.iter_content(block_size):
        prog_bar.update(len(chunk))
        fd.write(chunk)  # Write video chunk to disk
    prog_bar.close()

    # Validate file size on disk
    disk_size = os.path.getsize(full_filename)
    if disk_size == total_size:
      print(f"{Color.GREEN}File size matches Zoom cloud: {disk_size} bytes{Color.END}")
      return True
    else:
      print(
        f"{Color.RED}File size mismatch! Zoom: {total_size} bytes, "
        f"Disk: {disk_size} bytes{Color.END}"
      )
      return False

  except Exception as e:
    prog_bar.close()
    print(
      f"{Color.RED}### Error downloading file {recording_id}.{Color.END}"
    )
    return False


def load_completed_meeting_ids():
    try:
        with open(COMPLETED_MEETING_IDS_LOG, 'r') as fd:
            [COMPLETED_MEETING_IDS.add(line.strip()) for line in fd]

    except FileNotFoundError:
        print(
            f"{Color.DARK_CYAN}Log file not found. Creating new log file: {Color.END}"
            f"{COMPLETED_MEETING_IDS_LOG}\n"
        )


def handle_graceful_shutdown(signal_received, frame):
    print(f"\n{Color.DARK_CYAN}SIGINT or CTRL-C detected. system.exiting gracefully.{Color.END}")
    system.exit(0)


# ################################################################
# #                        MAIN                                  #
# ################################################################

def main():
    # clear the screen buffer
    #os.system('cls' if os.name == 'nt' else 'clear')

    # show the logo
    print(f"""
        {Color.DARK_CYAN}


         ,*****************.                  :+********+:          
      *************************              =+++******+==-         
    *****************************           =+++++****+=====.       
  *********************************        .=+++++++**+=======.      
 ******               ******* ******      :++++++++++==========:     
*******                .**    ******     :+++++++++=  -=========:    
*******                       ******/   -+++++++++=    -=========-   
*******                       /******  -+++++++++-      :=========-  
///////                 //    //////  =+++++++++-        :========== 
///////*              ./////.//////  =+++++++++-          :=++++++++=
 ////////////////////////////////*   -++++++++=------------=********=
   /////////////////////////////      :++++++=--------------=******= 
      /////////////////////////        .+++==----------------=****-  
         ,/////////////////              .==--------------------+*:    



              Zoom Recording Downloader 2 Google Drive

                      Version {APP_VERSION}

        {Color.END}
    """)

    load_access_token()

    load_completed_meeting_ids()

    print(f"{Color.BOLD}Getting user accounts...{Color.END}")
    users = get_users()

    for email, user_id, first_name, last_name in users:
        # Check if the email matches the desired user's email
        if email != 'val@bbooster.io':
            continue

        userInfo = (
            f"{first_name} {last_name} - {email}" if first_name and last_name else f"{email}"
        )
        print(f"\n{Color.BOLD}Getting recording list for {userInfo}{Color.END}")

        recordings = list_recordings(user_id)
        total_count = len(recordings)
        print(f"==> Found {total_count} recordings")

        for index, recording in enumerate(recordings):
          success = False
          all_files_downloaded = True  # Flag to check all files
          meeting_id = recording["uuid"]
          if meeting_id in COMPLETED_MEETING_IDS:
            print(f"==> Skipping already downloaded recording: {meeting_id}")
            continue

          try:
            downloads = get_downloads(recording)
          except Exception:
            print(
              f"{Color.RED}### No files found for recording with ID {recording['id']}.{Color.END}\n"
            )
            continue

          for file_type, file_extension, download_url, recording_type, recording_id in downloads:
            if recording_type != 'incomplete':
              filename, folder_name = format_filename({
                "file_type": file_type,
                "recording": recording,
                "file_extension": file_extension,
                "recording_type": recording_type,
                "recording_id": recording_id
              })

              print(
                f"==> Downloading ({index + 1} of {len(recordings)}) as {recording_type}: "
                f"{recording_id}"
              )

              if not download_recording(download_url, email, filename, folder_name):
                print(
                  f"{Color.RED}### Error downloading file {recording_id}.{Color.END}"
                )
                all_files_downloaded = False
            else:
              all_files_downloaded = False
              success = False

          if all_files_downloaded:
            print(f"{Color.GREEN}==> All files for recording with ID {meeting_id} were downloaded successfully.{Color.END}")
            if delete_recording(meeting_id):
              with open(COMPLETED_MEETING_IDS_LOG, 'a') as log:
                COMPLETED_MEETING_IDS.add(meeting_id)
                log.write(meeting_id + '\n')
                log.flush()
          else:
            print(
              f"{Color.YELLOW}==> Could not download all files for recording with ID {meeting_id}. Skipping deletion.{Color.END}"
            )
            success = False


            try:
                downloads = get_downloads(recording)
            except Exception:
                print(
                    f"{Color.RED}### Recording files missing for call with id {Color.END}"
                    f"'{recording['id']}'\n"
                )
                continue

            for file_type, file_extension, download_url, recording_type, recording_id in downloads:
                if recording_type != 'incomplete':
                    filename, folder_name = (
                        format_filename({
                            "file_type": file_type,
                            "recording": recording,
                            "file_extension": file_extension,
                            "recording_type": recording_type,
                            "recording_id": recording_id
                        })
                    )

                    # truncate URL to 64 characters
                    truncated_url = download_url[0:64] + "..."
                    print(
                        f"==> Downloading ({index + 1} of {total_count}) as {recording_type}: "
                        f"{recording_id}: {truncated_url}"
                    )
                    success |= download_recording(download_url, email, filename, folder_name)
                else:
                    print(
                        f"{Color.RED}### Incomplete Recording ({index + 1} of {total_count}) for "
                        f"recording with id {Color.END}'{recording_id}'"
                    )
                    success = False

            if success:
                # if successful, write the ID of this recording to the completed file
                with open(COMPLETED_MEETING_IDS_LOG, 'a') as log:
                    COMPLETED_MEETING_IDS.add(meeting_id)
                    log.write(meeting_id)
                    log.write('\n')
                    log.flush()

    print(f"\n{Color.BOLD}{Color.GREEN}*** All done! ***{Color.END}")
    save_location = os.path.abspath(DOWNLOAD_DIRECTORY)
    print(
        f"\n{Color.BLUE}Recordings have been saved to: {Color.UNDERLINE}{save_location}"
        f"{Color.END}\n"
    )


if __name__ == "__main__":
    # tell Python to shutdown gracefully when SIGINT is received
    signal.signal(signal.SIGINT, handle_graceful_shutdown)

    main()
