# Purge, command to delete data for videos that have already been uploaded (i.e. are no longer staged)

from vodbot.printer import cprint
from vodbot import util
from vodbot.stagedata import StageData
from os import listdir as os_listdir
from os.path import isfile
from pathlib import Path


def run(args):
	cprint("#dLoading config...#r", end=" ", flush=True)
	conf = util.load_conf(args.config)
	
	cprint("#dLoading stages...#r", end=" ", flush=True)
	VOD_DIR = Path(conf.directories.vods)
	STAGE_DIR = conf.directories.stage
	stagedatas = StageData.load_all_stages(STAGE_DIR)
	
	cprint("OK")
	
	# Get all staged video filenames (timestamp_id format)
	staged_video_files = set()
	for stage in stagedatas:
		for slice in stage.slices:
			# Extract just the filename without path or extension
			base_name = Path(slice.filepath).stem
			staged_video_files.add(base_name)
	
	# Find all video files that aren't referenced in stages
	video_files = set()
	for channel_dir in VOD_DIR.iterdir():
		if not channel_dir.is_dir():
			continue
			
		for file in channel_dir.iterdir():
			if not file.is_file():
				continue
				
			# Get base filename without extension
			if file.suffix in ['.mkv', '.meta', '.chat']:
				base_name = file.stem
				if base_name not in staged_video_files:
					video_files.add(file)
	
	if not video_files:
		cprint("#gNo unused videos found to purge.#r")
		return
	
	# Group files by base name for organized deletion
	grouped_files = {}
	for file in video_files:
		base_name = file.stem
		if base_name not in grouped_files:
			grouped_files[base_name] = []
		grouped_files[base_name].append(file)
	
	# Show confirmation prompt
	cprint(f"\n#yThe following {len(grouped_files)} videos will be purged:#r")
	for base_name in sorted(grouped_files.keys()):
		cprint(f"  {base_name}")
	
	response = input("\nContinue with deletion? [y/N] ").lower()
	if response != 'y':
		cprint("#rPurge cancelled.#r")
		return
	
	# Delete unused videos and associated files
	cprint(f"\n#yDeleting {len(grouped_files)} videos...#r")
	for base_name, files in grouped_files.items():
		cprint(f"#r- Deleting {base_name}...#r")
		for file in files:
			try:
				file.unlink()
				cprint(f"  #g{file.name} OK#r")
			except Exception as e:
				cprint(f"  #r{file.name} FAILED: {str(e)}#r")

	# Done!
	cprint("#gPurge complete!#r")
