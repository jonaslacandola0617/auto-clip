from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Protocol

from .models import (BoundingBox, CaptionLayout, EditSequence, EditorialAction, ManualCropOverride,
                     ReframeDecision, ReframePoint, ReframeTrack, SubjectTrack, SubjectTrackPoint,
                     VisualDetection, VisualEditPlan, VisualObservation, VisualShot)
from .time import MediaTime

DETECTOR_VERSION = "mediapipe-blazeface-short-range-v1"
DETECTOR_CONFIG = {"sample_interval": .5, "confidence": .55}
DETECTOR_ASSET_SHA256 = "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f"


def detector_asset_path() -> Path:
    asset = Path(__file__).resolve().parent / "assets" / "detectors" / "blaze_face_short_range.tflite"
    if not asset.is_file():
        raise RuntimeError("AutoClip's packaged face detector asset is unavailable.")
    if hashlib.sha256(asset.read_bytes()).hexdigest() != DETECTOR_ASSET_SHA256:
        raise RuntimeError("AutoClip's packaged face detector asset failed validation.")
    return asset


@dataclass(frozen=True, slots=True)
class Detection:
    at: MediaTime
    center_x: float
    center_y: float
    confidence: float
    width: float = .2
    height: float = .2

    @property
    def box(self) -> BoundingBox:
        return BoundingBox(max(0., self.center_x-self.width/2), max(0., self.center_y-self.height/2), min(self.width, 1.), min(self.height, 1.))


class SubjectDetector(Protocol):
    def detect(self, frames: Iterable[tuple[MediaTime, object]]) -> Iterable[list[Detection]]: ...


class MediaPipeFaceDetector:
    """Packaged offline detector; coordinates are normalized to proxy frames."""
    def __init__(self, model_path: str | None = None, *, confidence: float = .55) -> None:
        try:
            import cv2
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError("The local visual detector runtime is unavailable.") from exc
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(Path(model_path) if model_path else detector_asset_path())),
            running_mode=mp.tasks.vision.RunningMode.IMAGE, min_detection_confidence=confidence)
        self._detector = mp.tasks.vision.FaceDetector.create_from_options(options)
        self._mp, self._cv2 = mp, cv2

    def detect(self, frames: Iterable[tuple[MediaTime, object]]) -> Iterable[list[Detection]]:
        for at, frame in frames:
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB))
            height, width = frame.shape[:2]
            found = []
            for item in self._detector.detect(image).detections:
                box = item.bounding_box
                score = float(item.categories[0].score) if item.categories else 0.
                found.append(Detection(at, (box.origin_x+box.width/2)/width, (box.origin_y+box.height/2)/height, score, box.width/width, box.height/height))
            yield sorted(found, key=lambda detection: detection.confidence*detection.width*detection.height, reverse=True)


OpenCVFaceDetector = MediaPipeFaceDetector


@dataclass(frozen=True, slots=True)
class CoordinateMapper:
    source_width: int
    source_height: int
    proxy_width: int
    proxy_height: int
    def proxy_to_source(self, x: float, y: float) -> tuple[float, float]:
        return x*self.source_width/self.proxy_width, y*self.source_height/self.proxy_height
    def normalized_proxy_to_source_normalized(self, x: float, y: float) -> tuple[float, float]:
        px, py = self.proxy_to_source(x*self.proxy_width, y*self.proxy_height)
        return px/self.source_width, py/self.source_height


def build_reframe_track(track_id: str, timestamps: list[MediaTime], detections: list[Detection | None], *, smoothing_alpha: float=.2,
                        default_x: float=.5, default_y: float=.5, max_lost_frames: int=30, dead_zone: float=.06,
                        minimum_movement: float=.025, max_velocity_per_second: float=.12, minimum_confidence: float=.65,
                        manual_override: ManualCropOverride | None=None) -> ReframeTrack:
    if len(timestamps) != len(detections): raise ValueError("timestamps and detections must have equal length")
    if not 0 < smoothing_alpha <= 1: raise ValueError("smoothing_alpha must be in (0, 1]")
    points=[]; smooth_x,smooth_y=default_x,default_y; override=manual_override or ManualCropOverride(); previous=None
    for at,detection in zip(timestamps,detections,strict=True):
        if override.enabled:
            target_x,target_y,confidence,detected=override.crop_x,override.crop_y,None,False; smooth_x,smooth_y=target_x,target_y
        elif detection is not None and detection.confidence >= minimum_confidence:
            target_x,target_y,confidence,detected=detection.center_x,detection.center_y,detection.confidence,True
        else: target_x,target_y,confidence,detected=smooth_x,smooth_y,None,False
        if not override.enabled:
            if math.hypot(target_x-smooth_x,target_y-smooth_y) <= dead_zone: target_x,target_y=smooth_x,smooth_y
            move_x=smoothing_alpha*(target_x-smooth_x); move_y=smoothing_alpha*(target_y-smooth_y); distance=math.hypot(move_x,move_y)
            if distance >= minimum_movement:
                elapsed=float(at.seconds-previous.seconds) if previous is not None else 1.; maximum=max_velocity_per_second*max(elapsed,1/30)
                if distance > maximum: move_x*=maximum/distance; move_y*=maximum/distance
                smooth_x+=move_x; smooth_y+=move_y
        points.append(ReframePoint(at,target_x,target_y,smooth_x,smooth_y,override.scale if override.enabled else 1.,confidence,detected)); previous=at
    return ReframeTrack(track_id,points,{"algorithm":"thresholded_velocity_limited_ema","alpha":smoothing_alpha,"max_lost_frames":max_lost_frames,"lost_behavior":"hold_fixed","dead_zone":dead_zone,"minimum_movement":minimum_movement,"max_velocity_per_second":max_velocity_per_second,"minimum_confidence":minimum_confidence},override)


def crop_geometry(source_width:int,source_height:int,center_x:float,center_y:float,*,aspect_width:int=9,aspect_height:int=16,scale:float=1.) -> tuple[int,int,int,int]:
    crop_height=int(round(source_height/max(1.,scale))); crop_width=int(round(crop_height*aspect_width/aspect_height))
    if crop_width>source_width: crop_width=int(round(source_width/max(1.,scale))); crop_height=int(round(crop_width*aspect_height/aspect_width))
    crop_width-=crop_width%2; crop_height-=crop_height%2
    x=round(center_x*source_width-crop_width/2); y=round(center_y*source_height-crop_height*.38)
    return max(0,min(x,source_width-crop_width)),max(0,min(y,source_height-crop_height)),crop_width,crop_height


def build_subject_tracks(observations:list[VisualObservation],*,max_gap_samples:int=3,reacquire_distance:float=.18)->list[SubjectTrack]:
    tracks=[]; missing={}; last_scene={}
    for observation in observations:
        assigned=set()
        for detection in sorted(observation.detections,key=lambda item:item.confidence,reverse=True):
            cx=detection.box.x+detection.box.width/2; cy=detection.box.y+detection.box.height/2; candidates=[]
            for track in tracks:
                if track.id in assigned or missing.get(track.id,0)>max_gap_samples or not track.points or last_scene.get(track.id)!=observation.scene_id: continue
                box=track.points[-1].box; candidates.append((math.hypot(cx-(box.x+box.width/2),cy-(box.y+box.height/2)),track))
            distance,track=min(candidates,default=(float("inf"),None),key=lambda item:item[0])
            if track is None or distance>reacquire_distance: track=SubjectTrack(f"subject_{len(tracks)+1}",observation.source_id,[],detection.confidence); tracks.append(track)
            track.points.append(SubjectTrackPoint(observation.at,detection.box,detection.confidence)); track.confidence=sum(p.confidence for p in track.points)/len(track.points)
            missing[track.id]=0; last_scene[track.id]=observation.scene_id; assigned.add(track.id); detection.subject_id=track.id
        for track in tracks:
            if track.id not in assigned: missing[track.id]=missing.get(track.id,0)+1; track.lost_samples=max(track.lost_samples,missing[track.id])
    return tracks


def select_layout(detections:list[VisualDetection],*,active_subject_id:str|None=None)->tuple[str,list[str],list[BoundingBox],float,float]:
    confident=[item for item in detections if item.confidence>=.55]
    if not confident: return "fixed",[],[],.5,.5
    if len(confident)==1:
        item=confident[0]; return "single_subject",[item.subject_id],[],item.box.x+item.box.width/2,item.box.y+item.box.height/2
    first,second=confident[:2]; left=min(first.box.x,second.box.x); right=max(first.box.x+first.box.width,second.box.x+second.box.width)
    if right-left<=.56: return "two_person_wide",[first.subject_id,second.subject_id],[],(left+right)/2,.5
    if active_subject_id in {first.subject_id,second.subject_id}:
        chosen=first if first.subject_id==active_subject_id else second; return "speaker_focus",[chosen.subject_id],[],chosen.box.x+chosen.box.width/2,chosen.box.y+chosen.box.height/2
    return "split_screen",[first.subject_id,second.subject_id],[first.box,second.box],.5,.5


def _caption_layout(segment_id:str,detections:list[VisualDetection],requested:str="auto")->CaptionLayout:
    if requested in {"upper","center","lower"}: return CaptionLayout(segment_id,requested,110,"user-selected position")
    centers=[d.box.y+d.box.height/2 for d in detections if d.confidence>=.55]
    return CaptionLayout(segment_id,"upper" if centers and sum(centers)/len(centers)>.58 else "lower",110,"keeps captions away from detected face region")


def _visual_actions(sequence:EditSequence,emphasis:str,previous:VisualEditPlan|None)->list[EditorialAction]:
    if emphasis=="off": return []
    enabled={a.id:a.enabled for a in (previous.visual_actions if previous else [])}; candidates=[s for s in sequence.segments if s.purpose.lower() in {"hook","reaction","payoff"} and float(s.source_out.seconds-s.source_in.seconds)>=2]
    chosen=candidates[:1]+([candidates[-1]] if len(candidates)>1 and candidates[-1].id!=candidates[0].id else []); actions=[]
    for segment in chosen:
        duration=float(segment.source_out.seconds-segment.source_in.seconds); start=segment.timeline_start+min(.65,duration*.2); end=min(segment.timeline_start+duration,start+min(1.8,duration*.4)); action_id=f"visual_punch_{segment.id}"
        actions.append(EditorialAction(action_id,"punch_in",start,end,{"scale":1.10},f"restrained {segment.purpose.lower()} emphasis",enabled.get(action_id,True),{"source":"phase2b_local_policy"}))
    return actions


def build_visual_edit_plan(sequence:EditSequence,observations:list[VisualObservation],*,previous:VisualEditPlan|None=None,framing_mode:str="auto",visual_emphasis:str="automatic",caption_preset:str="word_highlight",caption_position:str="auto")->VisualEditPlan:
    tracks=build_subject_tracks(observations); actions=_visual_actions(sequence,visual_emphasis,previous); decisions=[]; shots=[]; layouts=[]; warnings=[]
    for segment in sequence.segments:
        in_s,out_s=float(segment.source_in.seconds),float(segment.source_out.seconds); relevant=[o for o in observations if in_s<=float(o.at.seconds)<=out_s]; detections=[d for o in relevant for d in o.detections]
        if framing_mode=="fixed": layout,ids,panels,cx,cy="fixed",[],[],.5,.5
        else: layout,ids,panels,cx,cy=select_layout(relevant[len(relevant)//2].detections if relevant else [])
        if not detections: warnings.append(f"{segment.id}: no confident subject; stable center framing used.")
        layouts.append(_caption_layout(segment.id,detections,caption_position)); end_t=segment.timeline_start+out_s-in_s
        active=[a for a in actions if a.enabled and segment.timeline_start<=a.timeline_start<end_t]; boundaries=[segment.timeline_start,end_t]
        for action in active: boundaries.extend([action.timeline_start,action.timeline_end])
        boundaries=sorted({max(segment.timeline_start,min(end_t,value)) for value in boundaries})
        for index,(start_t,stop_t) in enumerate(zip(boundaries,boundaries[1:],strict=False)):
            current=[a for a in active if a.timeline_start<=start_t+.001 and a.timeline_end>=stop_t-.001]; scale=float(current[0].parameters.get("scale",1.)) if current else 1.; decision_id=f"reframe_{segment.id}_{index}"
            decisions.append(ReframeDecision(decision_id,segment.id,start_t,stop_t,layout,cx,cy,max(1.,min(1.2,scale)),ids,panels,current[0].reason if current else "hard-cut stable composition"))
            offset=start_t-segment.timeline_start; duration=stop_t-start_t
            shots.append(VisualShot(f"shot_{segment.id}_{index}",segment.id,MediaTime.from_seconds(in_s+offset,segment.source_in.time_base,exact=False),MediaTime.from_seconds(in_s+offset+duration,segment.source_out.time_base,exact=False),start_t,decision_id,[a.id for a in current]))
    revision=previous.revision+1 if previous else 1
    return VisualEditPlan(f"visual_{sequence.id}",sequence.id,shots,observations,tracks,decisions,layouts,actions,warnings,revision=revision,sequence_revision=sequence.revision,framing_mode=framing_mode,visual_emphasis=visual_emphasis,caption_preset=caption_preset,caption_position=caption_position)


def visual_cache_key(sequence:EditSequence,source_fingerprint:dict[str,object],*,detector_version:str=DETECTOR_VERSION,config:dict[str,object]|None=None)->str:
    payload={"sequence_id":sequence.id,"sequence_revision":sequence.revision,"source":source_fingerprint,"detector":detector_version,"config":config or DETECTOR_CONFIG}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:24]


def analyze_visual_observations(source:Path,sequence:EditSequence,*,detector:SubjectDetector|None=None,sample_interval:float=.5)->tuple[list[VisualObservation],list[str]]:
    warnings=[]
    try: active=detector or MediaPipeFaceDetector(); cv2=active._cv2  # type: ignore[attr-defined]
    except Exception as exc: return [],[f"Face detection could not start; stable center framing will be used ({type(exc).__name__})."]
    capture=cv2.VideoCapture(str(source))
    if not capture.isOpened(): return [],["Source video could not be opened; stable center framing will be used."]
    observations=[]
    try:
        for segment in sequence.segments:
            frames=[]; current=float(segment.source_in.seconds); stop=float(segment.source_out.seconds)
            while current<=stop:
                capture.set(cv2.CAP_PROP_POS_MSEC,current*1000); ok,frame=capture.read()
                if not ok: break
                at=MediaTime.from_seconds(Fraction(str(current)),segment.source_in.time_base,exact=False); frames.append((at,frame)); current+=sample_interval
            try: results=list(active.detect(frames))
            except Exception as exc: warnings.append(f"Detection failed in {segment.id}; center framing used ({type(exc).__name__})."); results=[[] for _ in frames]
            for index,((at,_),found) in enumerate(zip(frames,results,strict=False)):
                observations.append(VisualObservation(f"obs_{segment.id}_{index}",segment.source_id,at,segment.id,[VisualDetection("","face",item.box,item.confidence) for item in found]))
    finally: capture.release()
    return observations,warnings


def analyze_video_clip(source:Path,start:MediaTime,end:MediaTime,track_id:str,*,sample_interval:float=.5,manual_override:ManualCropOverride|None=None)->ReframeTrack:
    if end.seconds<=start.seconds: raise ValueError("invalid clip range")
    if manual_override and manual_override.enabled: return build_reframe_track(track_id,[start,end],[None,None],manual_override=manual_override)
    try: detector=OpenCVFaceDetector(); cv2=detector._cv2
    except Exception: return build_reframe_track(track_id,[start,end],[None,None])
    capture=cv2.VideoCapture(str(source))
    if not capture.isOpened(): return build_reframe_track(track_id,[start,end],[None,None])
    timestamps=[]; frames=[]; current=float(start.seconds); stop=float(end.seconds)
    try:
        while current<=stop:
            capture.set(cv2.CAP_PROP_POS_MSEC,current*1000); ok,frame=capture.read()
            if not ok: break
            at=MediaTime.from_seconds(Fraction(str(current)),start.time_base,exact=False); timestamps.append(at); frames.append((at,frame)); current+=sample_interval
    finally: capture.release()
    if not timestamps: return build_reframe_track(track_id,[start,end],[None,None])
    try: primary=[items[0] if items else None for items in detector.detect(frames)]
    except Exception: primary=[None for _ in timestamps]
    return build_reframe_track(track_id,timestamps,primary)
