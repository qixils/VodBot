# Module that manages shelling out commands to FFmpeg, with functions returning paths to the final video.

from .printer import cprint
from .stagedata import StageData, VideoSlice
from .config import Config

import os
import subprocess
import shutil
from pathlib import Path
from typing import List


class VideoFailure(Exception):
	def __init__(self, video_id:str="unknown") -> None:
		self.video_id = video_id
		super().__init__(self.video_id)

class FailedToSlice(VideoFailure):
	pass

class FailedToConcat(VideoFailure):
	pass

class FailedToCleanUp(VideoFailure):
	pass


def slice_video(TEMP_DIR: Path, LOG_LEVEL: str, vslice: VideoSlice, REDIRECT: Path, i: int, total: int) -> tuple[Path, bool]:
	tmpfile = TEMP_DIR / f"{vslice.video_id}={i}.mp4"
	cprint(f"#rSlicing stage part ({i+1}/{total}) `#fM{vslice.video_id}#r` #d({vslice.ss} - {vslice.to})#r")

	# If we're using the entire video, just return the source file
	if vslice.ss == "0:0:0" and vslice.to == "EOF":
		return Path(vslice.filepath), True

	cmd = [ "ffmpeg", "-hide_banner", "-ss", vslice.ss ]

	if vslice.to != "EOF":
		cmd += ["-to", vslice.to]

	cmd += [
		"-i", vslice.filepath, "-c", "copy",
		str(tmpfile), "-y", "-stats", "-loglevel", LOG_LEVEL
	]
	
	redirect = subprocess.DEVNULL
	if REDIRECT != Path():
		redirect = open(REDIRECT, "w")
	result = subprocess.run(cmd, stderr=redirect)
	if REDIRECT != Path():
		redirect.close()

	if result.returncode != 0:
		raise FailedToSlice(vslice.video_id)
	
	return tmpfile, False


def concat_video(TEMP_DIR: Path, LOG_LEVEL: str, stage_id: str, slice_paths: List[Path], REDIRECT: Path) -> Path:
	# first create file list in temp dir
	list_path = TEMP_DIR / f"concat-{stage_id}.txt"
	with open(str(list_path), "w") as f:
		for path in slice_paths:
			f.write(f"file '{path.name}'\n")

	# then do subprocess for concat list
	concat_path = TEMP_DIR / f"concat-{stage_id}.mp4"
	cmd = [
		"ffmpeg", "-hide_banner", "-f", "concat",
		"-safe", "0", "-i", str(list_path),
		"-c", "copy", str(concat_path),
		"-y", "-stats", "-loglevel", LOG_LEVEL
	]
	
	cprint(f"#rConcatenating videos for `#fM{stage_id}#r`")
	
	# we need to hop directories real fast to perform this function.
	# we'll come back when we're done.
	cwd = os.getcwd()
	os.chdir(str(TEMP_DIR))

	redirect = subprocess.DEVNULL
	if REDIRECT != Path():
		redirect = open(REDIRECT, "w")
	result = subprocess.run(cmd, stderr=redirect, check=True)
	if REDIRECT != Path():
		redirect.close()
	os.chdir(cwd)

	if result.returncode != 0:
		raise FailedToConcat()

	cprint(f"Cleaning up after stage `#fM{stage_id}#r`...")

	try:
		os.remove(str(list_path))
	except Exception as e:
		raise FailedToCleanUp(e)

	return concat_path


def process_stage(conf: Config, stage: StageData) -> tuple[Path, bool]:
	tempdir = Path(conf.directories.temp)
	loglevel = conf.export.ffmpeg_loglevel

	# slice all the slices
	slices = len(stage.slices)
	slice_results = [slice_video(tempdir, loglevel, stage.slices[x], conf.export.ffmpeg_stderr, x, slices) for x in range(slices)]
	
	# Unzip the results
	slice_paths, is_source = zip(*slice_results)

	# edge case of one video - pass through the is_source flag
	if len(slice_paths) == 1:
		return slice_paths[0], is_source[0]

	# concat all the slices
	concat_path = concat_video(tempdir, loglevel, stage.id, slice_paths, conf.export.ffmpeg_stderr)

	# clean up old slices, but only those that aren't source files
	for path, src in zip(slice_paths, is_source):
		if not src:
			try:
				os.remove(path)
			except Exception as e:
				raise FailedToCleanUp(e)

	# return the path of the concated vid (never a source file)
	return concat_path, False
