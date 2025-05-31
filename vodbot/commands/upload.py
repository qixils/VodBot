# Upload, command to upload staged videos to YouTube
# References:
# https://developers.google.com/docs/api/quickstart/python
# https://developers.google.com/youtube/v3/guides/uploading_a_video
# https://learndataanalysis.org/how-to-upload-a-video-to-youtube-using-youtube-data-api-in-python/

from vodbot.stagedata import StageData

import vodbot.video as vbvid
import vodbot.chatlog as vbchat
import vodbot.thumbnail as vbthumbnail
from vodbot.util import exit_prog, load_conf, format_size, safe_append_line
from vodbot.cache import load_cache, save_cache
from vodbot.printer import cprint
from vodbot.config import Config
from vodbot.webhook import init_webhooks, send_upload_error, send_upload_video, send_upload_job_done

import base64
import requests
import json
import time
from datetime import datetime
from os import remove as os_remove
from os.path import exists as os_exists
from time import sleep
from typing import List
import re
from csv import writer as csv_writer

from httplib2.error import HttpLib2Error, HttpLib2ErrorWithResponse

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError, ResumableUploadError
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials


RETRIABLE_EXCEPTS = (HttpLib2Error, HttpLib2ErrorWithResponse, IOError)


EPOCH = datetime.utcfromtimestamp(0)
def sort_stagedata(stagedata):
    date = datetime.strptime(stagedata.datestring, "%Y/%m/%d")
    return (date - EPOCH).total_seconds()


def _upload_artifact(upload_string, response_upload, getting_video=False, filesize=0):
    video_id = "" # youtube video id
    resp = None
    errn = 0
    errn_max = 50

    uploaded = 0

    def print_error(f:List, secs:int=10):
        nonlocal errn, errn_max
        f = [str(x) for x in f]
        cprint(f"#fY#dWARN: An HTTP error has occurred ({errn}/{errn_max}), retyring in {secs} seconds... ({', '.join(f)})#r")
        errn += 1
        sleep(secs)

    while resp is None:
        try:
            status, resp = response_upload.next_chunk()

            progress = status.progress()*100 if status else 100
            uploaded = status.resumable_progress if status else uploaded
            # filesize = status.total_size if status else filesize
            if not status:
                uploaded = filesize
            
            su = format_size(uploaded, units=False)
            st = format_size(filesize)
            sp = f"#d({su}/{st})...#r"
            cprint(f"#c#fCUploading {upload_string}: #fC{progress:.1f}#fY%#r {sp}", end='\r')

            if resp is not None and getting_video:
                video_id = resp["id"]
        except ResumableUploadError as err:
            if err.resp.status in [400, 401, 402, 403]:
                try:
                    jsondata = json.loads(err.content)['error']['errors'][0]
                    exit_prog(40, f"API Error: `{jsondata['reason']}`. Message: `{jsondata['message']}`")
                except (json.JSONDecodeError, KeyError):
                    exit_prog(40, f"Unknown API Error has occured, ({err.resp.status}, {err.content})")
            print_error([err.resp.status, err.content])
        except HttpError as err:
            if err.resp.status in [500, 502, 503, 504]:
                print_error([err.resp.status, err.content])
            else:
                exit_prog(40, f"Unknown API Error has occured, ({err.resp.status}, {err.content})")
        except RETRIABLE_EXCEPTS as err:
            print_error([err])

        if errn >= errn_max:
            cprint("#fY#dWARN: Skipping upload, errored too many times.#r")
            return None
    
    # extra newline when done
    print()
    
    if getting_video:
        return video_id
    else:
        return True


def upload_video(conf: Config, service: Resource, stagedata: StageData) -> str:
    tmpfile = None
    is_source = False
    try:
        tmpfile, is_source = vbvid.process_stage(conf, stagedata)
    except vbvid.FailedToSlice as e:
        cprint(f"#r#fRSkipping stage `{stagedata.id}`, failed to slice video with ID of `{e.vid}`.#r\n")
    except vbvid.FailedToConcat:
        cprint(f"#r#fRSkipping stage `{stagedata.id}`, failed to concatenate videos.#r\n")
    except vbvid.FailedToCleanUp as e:
        cprint(f"#r#fRSkipping stage `{stagedata.id}`, failed to clean up temp files.#r\n\n{e.vid}")

    # send request to youtube to upload
    request_body = {
        "snippet": {
            "categoryId": 20,
            "title": stagedata.title,
            "description": stagedata.desc
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False
        }
    }

    # create media file, upload in chunks
    print(f"Chunk size: {conf.upload.chunk_size}")
    media_file = MediaFileUpload(str(tmpfile), chunksize=conf.upload.chunk_size, resumable=True)

    # create upload request and execute
    response_upload = service.videos().insert(
        part="snippet,status",
        body=request_body,
        notifySubscribers=conf.upload.notify_subscribers,
        media_body=media_file
    )

    filesize = media_file.size()

    cprint(f"#c#fCUploading stage video #r`#fM{stagedata.id}#r`: #fC0#fY%#r #d(0/{format_size(filesize)})...#r", end="\r")
    uploaded = _upload_artifact(f"stage video #r`#fM{stagedata.id}#r`", response_upload, getting_video=True, filesize=filesize)

    try:
        # delete vars to release the files
        del media_file
        del response_upload
         # Only remove if it's not a source file
        if not is_source:
            os_remove(str(tmpfile))
    except Exception as e:
        exit_prog(90, f"Failed to remove temp video slice file of stage `{stagedata.id}` after upload. {e}")
    
    return uploaded


def upload_captions(conf: Config, service: Resource, stagedata: StageData, vid_id: str) -> bool:
    tmpfile = vbchat.process_stage(conf, stagedata, "upload")

    if not tmpfile:
        return False

    request_body = {
        "snippet": {
            "name": "Chat",
            "videoId": vid_id,
            "language": "en"
        }
    }

    media_file = MediaFileUpload(str(tmpfile), chunksize=conf.upload.chunk_size, resumable=True)

    response_upload = service.captions().insert(
        part="snippet",
        body=request_body,
        sync=False,
        media_body=media_file
    )

    filesize = media_file.size()

    cprint(f"#c#fCUploading stage chatlog #r`#fM{stagedata.id}#r`: #fC0#fY%#r #d(0/{format_size(filesize)})...#r", end="\r")
    uploaded = _upload_artifact(f"stage chatlog #r`#fM{stagedata.id}#r`", response_upload, filesize=filesize)
    
    try:
        # delete vars to release the files
        del media_file
        del response_upload
        # sleep(1)
        os_remove(str(tmpfile))
    except Exception as e:
        exit_prog(90, f"Failed to remove temp chatlog file of stage `{stagedata.id}` after upload. {e}")

    return uploaded


def upload_thumbnail(conf: Config, service: Resource, stagedata: StageData, vid_id: str) -> bool:
    tmpfile = False

    try:
        tmpfile = vbthumbnail.generate_thumbnail(conf, stagedata)
    except vbthumbnail.ScreengrabFailed:
        cprint("#d#fYWARN: Failed to get screenshot from video with FFMPEG for thumbnail. Skipping...#r")
        return False

    if not tmpfile:
        return False

    media_file = MediaFileUpload(str(tmpfile), chunksize=conf.upload.chunk_size, resumable=True)

    # this may need to be a straight upload, not resumable
    # see: https://developers.google.com/youtube/v3/docs/thumbnails/set
    response_upload = service.thumbnails().set(
        videoId=vid_id,
        media_body=media_file
    )

    filesize = media_file.size()

    cprint(f"#c#fCUploading stage thumbnail #r`#fM{stagedata.id}#r`: #fC0#fY%#r #d(0/{format_size(filesize)})...#r", end="\r")
    uploaded = _upload_artifact(f"stage thumbnail #r`#fM{stagedata.id}#r`", response_upload, filesize=filesize)

    try:
        # delete vars to release the files
        del media_file
        del response_upload
        # sleep(1)
        os_remove(str(tmpfile))
    except Exception as e:
        exit_prog(90, f"Failed to remove temp thumbnail file of stage `{stagedata.id}` after upload. {e}")

    return uploaded


def _download_credentials(conf: Config) -> None:
    CLIENT_FILE = conf.upload.client_path
    CLIENT_FILE_URL = conf.upload.client_url

    # download the credentials
    try:
        r = requests.get(CLIENT_FILE_URL)
        r.raise_for_status()
        decoded = base64.b64decode(r.content, validate=True)
        with open(CLIENT_FILE, "wb") as f:
            f.write(decoded)
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout, requests.TooManyRedirects) as e:
        exit_prog(13, f"Failed to GET credentials from url `{conf.upload.client_url}`, error: \n{e}")
    except base64.binascii.Error as e:
        exit_prog(13, f"Failed to decode downloaded credentials, error: \n{e}")
    except IOError as e:
        exit_prog(13, f"Failed to write credentials to path `{conf.upload.client_path}`, error: \n{e}")
    except Exception as e:
        exit_prog(13, f"Unknown exception occurred when pulling YouTube credentials, error: \n{e}")


def get_credentials(conf:Config, SCOPES:List[str]) -> Credentials:
    CLIENT_FILE = conf.upload.client_path
    got_new_creds = False

    if not CLIENT_FILE.is_file():
        if CLIENT_FILE.exists():
            exit_prog(12, f"Something that isn't a file exists at `{CLIENT_FILE}` and should be removed.")
        else:
            cprint("#dDownloading new credentials...")
            _download_credentials(conf)
            got_new_creds = True
    else:
        cprint()

    creds = None

    cprint("#dNOTE: If Google gives an error, you will need to exit and check `client_url` in your configuration.")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
        creds = flow.run_local_server(port=conf.upload.oauth_port)
    except KeyboardInterrupt as e:
        # credentials were trash, we need to reset them
        if got_new_creds:
            cprint("#d#fYWARN: Exited during OAuth authentication with brand new credentials, deleting credentials assuming they are bad.")
            os_remove(CLIENT_FILE)
        raise e

    # add a check to the above functions for if the credentials are invalid.
    # should also add a flag for if they were already downloaded so we can shortcut to an error that the new credentials are already bad and delete them

    return creds

def scan_videos(conf: Config, service: Resource) -> None:
    cprint("#dScanning YouTube channel for Twitch VOD references...")
    page_token = None
    matches = []
    seen_twitch_ids = set()
    missing_snippet = []
    invalid_status = []
    duplicate_twitch_ids = []
    pattern = re.compile(r"https://twitch\.tv/videos/(\d+)")
    part_pattern = re.compile(r"\(p\.(\d)\)$")

    errored_video_ids = set()

    while True:
        # Retry logic for HttpError
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                response = service.search().list(
                    part="snippet",
                    forMine=True,
                    maxResults=50,
                    type="video",
                    pageToken=page_token
                ).execute()
                break
            except HttpError as e:
                if attempt < max_attempts - 1:
                    cprint(f"#fY#dWARN: HttpError on request (attempt {attempt+1}/{max_attempts}), retrying in 1s...#r")
                    time.sleep(1)
                else:
                    exit_prog(60, f"Failed to fetch videos from YouTube API after {max_attempts} attempts: {e}")

        # Gather video IDs from search results
        video_items = response.get('items', [])
        video_ids = [item['id']['videoId'] for item in video_items if 'id' in item and 'videoId' in item['id']]

        # Fetch video details for uploadStatus validation
        video_statuses = {}
        if video_ids:
            for attempt in range(max_attempts):
                try:
                    videos_resp = service.videos().list(
                        part="status",
                        id=",".join(video_ids)
                    ).execute()
                    break
                except HttpError as e:
                    if attempt < max_attempts - 1:
                        cprint(f"#fY#dWARN: HttpError on videos().list (attempt {attempt+1}/{max_attempts}), retrying in 1s...#r")
                        time.sleep(1)
                    else:
                        exit_prog(60, f"Failed to fetch video statuses from YouTube API after {max_attempts} attempts: {e}")
            for v in videos_resp.get('items', []):
                vid = v.get('id')
                status = v.get('status', {})
                video_statuses[vid] = status

        # Iterate through search results and corresponding video statuses
        for item in video_items:
            video_id = item['id']['videoId']
            snippet = item.get('snippet')
            if not snippet:
                missing_snippet.append(video_id)
                errored_video_ids.add(video_id)
                continue

            ## Check uploadStatus
            ## [Seems like the uploads are fine and this actually causes some false positives]
            status = video_statuses.get(video_id, {})
            upload_status = status.get('uploadStatus')
            if upload_status != "processed":
                # errored_video_ids.add(video_id)
                invalid_status.append((video_id, upload_status))
                continue

            desc = snippet.get('description', '')
            title = snippet.get('title', '')

            match = pattern.search(desc)
            if not match:
                continue

            twitch_id = match.group(1)
            part_match = part_pattern.search(title)
            if part_match:
                twitch_id = f"{twitch_id}_p{part_match.group(1)}"
            
            if twitch_id in seen_twitch_ids:
                duplicate_twitch_ids.append((video_id, twitch_id))
                errored_video_ids.add(video_id)
                continue
            
            seen_twitch_ids.add(twitch_id)
            matches.append((video_id, twitch_id))
            cprint(f"#dFound match: #fCYouTube #r#fM{video_id} #fC-> Twitch #r#fM{twitch_id}#r")

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    if matches:
        output_file = conf.directories.vods / "twitch_matches.csv"
        try:
            with open(output_file, 'w', newline='') as f:
                writer = csv_writer(f)
                # writer.writerow(['youtube_id', 'twitch_id'])
                writer.writerows(matches)
            cprint(f"#fGWrote {len(matches)} matches to {output_file}#r")
        except IOError as e:
            exit_prog(61, f"Failed to write matches to CSV: {e}")
    else:
        cprint("#dNo matches found.")

    # Summarize errors
    if missing_snippet:
        cprint(f"#fY#dWARN: {len(missing_snippet)} videos missing snippet: {', '.join(missing_snippet)}#r")
    if invalid_status:
        cprint(f"#fY#dWARN: {len(invalid_status)} videos with invalid upload status:")
        for vid, status in invalid_status:
            cprint(f"  YouTube {vid} -> Status: {status}")
    if duplicate_twitch_ids:
        cprint(f"#fY#dWARN: {len(duplicate_twitch_ids)} duplicate twitch_ids found:")
        for vid, tid in duplicate_twitch_ids:
            cprint(f"  YouTube {vid} -> Twitch {tid}")

    if errored_video_ids:
        cprint(f"#fY#dSummary: {len(errored_video_ids)} video(s) had errors and can be deleted.#r")
        try:
            resp = input("Delete errored videos from YouTube? (y/N): ").strip().lower()
        except EOFError:
            resp = "n"
        if resp == "y":
            for vid in errored_video_ids:
                try:
                    service.videos().delete(id=vid).execute()
                    cprint(f"#fGDeleted video {vid}#r")
                except Exception as e:
                    cprint(f"#fRFailed to delete video {vid}: {e}#r")

def run(args):
    # load config
    conf = load_conf(args.config)
    cache = load_cache(conf, args.cache_toggle)
    init_webhooks(conf)

    # configure variables
    STAGE_DIR = conf.directories.stage
    SESSION_FILE = conf.upload.session_path
    CLIENT_FILE = conf.upload.client_path

    API_NAME = 'youtube'
    API_VERSION = 'v3'
    SCOPES = [ # Only force-ssl is required, but both makes it explicit.
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl"
    ]

    # handle logout
    if args.id == "logout":
        try:
            os_remove(SESSION_FILE)
            cprint("#dLogged out of Google API session#r")
        except:
            exit_prog(11, "Failed to remove credentials for YouTube account.")
        
        return

    cprint("Authenticating with Google...", end=" ")

    service: Resource = None
    creds = None

    if os_exists(SESSION_FILE):
        creds = Credentials.from_authorized_user_file(SESSION_FILE, SCOPES)
    
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = get_credentials(conf, SCOPES)
        except RefreshError:
            creds = get_credentials(conf, SCOPES)
        
        with open(SESSION_FILE, "w") as f:
            f.write(creds.to_json())
    
    try:
        service = build(API_NAME, API_VERSION, credentials=creds)
    except Exception as err:
        exit_prog(50, f"Failed to connect to YouTube API, \"{err}\".")
    
    cprint("done.#r")

    # handle scan
    if args.id == "scan":
        scan_videos(conf, service)
        return
    
    # load stages
    # Handle id/all
    stagedatas = None
    if args.id == "all":
        cprint("#dLoading stages...", end=" ")
        # create a list of all the hashes and sort by date streamed, upload chronologically
        stagedatas = StageData.load_all_stages(STAGE_DIR)
        stagedatas.sort(key=sort_stagedata)
    else:
        cprint("#dLoading stage...", end=" ")
        # check if stage exists, and prep it for upload
        stagedatas = [StageData.load_from_id(STAGE_DIR, args.id)]
    
    # begin to upload
    finished_jobs = 0
    cprint(f"#dAbout to upload {len(stagedatas)} stage(s).#r")
  
    for stage in stagedatas:
        video_id = upload_video(conf, service, stage)
        if video_id is not None:
            if conf.upload.thumbnail_enable:
                if not upload_thumbnail(conf, service, stage, video_id):
                    t = f"Failed to upload video thumbnail for stage `{stage.id}`, video ID `{video_id}`."
                    cprint(f"#fY#dWARN: {t} Skipping...#r")
                    send_upload_error(t)

            if conf.upload.chat_enable:
                if not upload_captions(conf, service, stage, video_id):
                    t = f"Failed to upload chat captions for stage `{stage.id}`, video ID `{video_id}`."
                    cprint(f"#fY#dWARN: {t} Skipping...#r")
                    send_upload_error(t)
            
            cprint(f"#l#fGVideo was successfully uploaded!#r #dhttps://youtu.be/{video_id}#r")

            safe_append_line(conf.directories.vods / "uploads.csv", f"{video_id},{','.join(slice.video_id for slice in stage.slices)}")

            if conf.stage.delete_on_upload:
                try:
                    os_remove(STAGE_DIR / f"{stage.id}.stage")
                    # cache.stages.remove(stage.id)
                    # save_cache(conf, cache)
                except:
                    send_upload_error(f"Failed to remove stage `{stage.id}` after upload.")
                    if len(stagedatas) < 1:
                        exit_prog(90, f"Failed to remove stage `{stage.id}` after upload.")
                    else:
                        cprint(f"#fR#lFailed to remove stage `{stage.id}` after upload.#r")
            finished_jobs += 1
            send_upload_video(stage, f"https://youtu.be/{video_id}")
        else:
            send_upload_error(f"Failed to upload stage `{stage.id}`.")
    
    if len(stagedatas) > 1:
        send_upload_job_done(finished_jobs, len(stagedatas))
