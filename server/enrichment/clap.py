"""CLAP: audio and text embedded in one space, so a phrase ("rolling, hypnotic, no vocals") can be matched against
what tracks actually sound like.

laion/larger_clap_general is used because larger_clap_music doesn't load correctly in Transformers 5 (its similarities
come out flat); the general model separates music styles well in testing.
"""

from functools import cache

MODEL = "laion/larger_clap_general"
SAMPLE_RATE = 48000
WINDOW = 10 * SAMPLE_RATE  # CLAP looks at 10 seconds; a 30-second preview gives three windows


@cache
def _model():
    import torch
    from transformers import ClapModel, ClapProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return ClapModel.from_pretrained(MODEL).eval().to(device), ClapProcessor.from_pretrained(MODEL), device


def embed_audio(path):
    """Unit-length 512-d vector: the mean of the preview's 10-second windows."""
    import essentia
    import essentia.standard as es
    import torch

    essentia.log.warningActive = False
    model, processor, device = _model()
    audio = es.MonoLoader(filename=path, sampleRate=SAMPLE_RATE)()
    windows = [audio[start : start + WINDOW] for start in range(0, max(len(audio) - WINDOW // 2, 1), WINDOW)]
    with torch.no_grad():
        inputs = processor(audio=windows, sampling_rate=SAMPLE_RATE, return_tensors="pt").to(device)
        embeds = model.audio_model(input_features=inputs["input_features"], is_longer=inputs.get("is_longer")).pooler_output
        embeds = torch.nn.functional.normalize(model.audio_projection(embeds), dim=-1).mean(0)
    return torch.nn.functional.normalize(embeds, dim=0).cpu().tolist()


def embed_text(texts):
    """Unit-length 512-d vectors, one per text."""
    import torch

    model, processor, device = _model()
    with torch.no_grad():
        inputs = processor(text=texts, return_tensors="pt", padding=True).to(device)
        embeds = model.text_model(input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]).pooler_output
        embeds = torch.nn.functional.normalize(model.text_projection(embeds), dim=-1)
    return embeds.cpu().tolist()
