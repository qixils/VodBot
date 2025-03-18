from dataclasses import dataclass, field
import json
from pathlib import Path
from random import choice
from typing import List, Optional
from dataclasses_json import dataclass_json
from string import ascii_lowercase, digits as ascii_digits
from os import listdir as os_listdir
from os.path import isfile, isdir

from vodbot import util


@dataclass_json
@dataclass
class VideoSlice():
	video_id: str
	ss: str
	to: str
	filepath: str


@dataclass_json
@dataclass
class ThumbnailData():
	heads: List[str]
	game: str
	text: str
	video_slice_id: int
	timestamp: str


@dataclass_json
@dataclass
class StageData():
	title: str
	desc: str
	streamers: List[str]
	datestring: str

	slices: List[VideoSlice]
	thumbnail: Optional[ThumbnailData] = None

	id: str = field(default_factory=lambda: 
		"".join([choice(ascii_lowercase + ascii_digits) for _ in range(4)]))
	
	def write_stage(self, filename):
		with open(filename, "w") as f:
			f.write(self.to_json())
	
	@staticmethod
	def load_from_id(stagedir: Path, sid: str) -> 'StageData':
		jsonread = None
		try:
			with open(stagedir / f"{sid}.stage") as f:
				jsonread = json.load(f)
		except FileNotFoundError:
			util.exit_prog(46, f'Could not find stage "{sid}". (FileNotFound)')
		except KeyError:
			util.exit_prog(46, f'Could not parse stage "{sid}" as JSON. Is this file corrupted?')
		
		return StageData.from_dict(jsonread)
	
	@staticmethod
	def load_all_stages(stagedir: Path) -> List['StageData']:
		stages = []
		for d in os_listdir(stagedir):
			if isfile(stagedir / d) and d.endswith(".stage"):
				stages.append(StageData.load_from_id(stagedir, d[:-6]))
		
		return stages