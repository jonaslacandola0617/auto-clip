# AutoClip third-party notices

AutoClip includes open-source runtime components. This inventory is informational and is not legal advice. Release owners must preserve the license texts required by the exact artifacts shipped.

| Component | Purpose | Reported license | Release note |
| --- | --- | --- | --- |
| Tauri and Tauri plugins | Windows desktop shell | Apache-2.0 / MIT | Preserve upstream notices. |
| React and React DOM | User interface | MIT | Preserve upstream notice. |
| Python | Packaged worker runtime | PSF-2.0 | Include Python license with the frozen runtime. |
| PyInstaller | Worker freezing | GPL-2.0-or-later with bootloader exception | Confirm the exception and include its copyright notice. |
| faster-whisper | Local transcription adapter | MIT | Model files have their own metadata. |
| CTranslate2 | Local inference runtime | MIT | Native libraries are packaged with the worker. |
| MediaPipe | Face detection runtime | Apache-2.0 | Preserve NOTICE where distributed. |
| BlazeFace short-range model | Packaged face detector asset | Upstream MediaPipe asset; manual review required | Confirm model provenance and redistribution terms before public release. |
| OpenCV | Vision fallback | Apache-2.0 | Headless Python distribution. |
| FFmpeg / ffprobe 8.0.1 essentials build from gyan.dev | Media inspection and rendering | GPL-3.0-or-later for the exact bundled static build | The binary reports `--enable-gpl --enable-version3 --enable-static` and includes libx264. Public distribution is blocked until the complete GPL license materials and a compliant corresponding-source distribution or written offer are packaged and reviewed. |
| Hugging Face Hub / tokenizers | Managed model download | Apache-2.0 | Transcription model licenses remain separate. |

The baseline Whisper model is downloaded on first use into AutoClip's managed model cache and is not embedded in the installer. Its repository metadata and license must be reviewed before public distribution.

This file is an inventory, not the complete license bundle. Version 1.0.0-rc.1 is an internal release candidate only. A public build must also ship the exact required license/copyright texts for every frozen Python dependency and detector/model asset, preserve notices, and pass a legal/compliance review.
