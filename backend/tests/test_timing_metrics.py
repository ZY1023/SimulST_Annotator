from app.timing_metrics import derive, overlap_ms


def state(id, start, end, text):
    return {"id": id, "media_id": "synthetic", "start_ms": start, "end_ms": end,
            "text": text, "precision": "frame", "active": 1}


def test_distinguishes_visible_no_update_blank_and_speech_overlap():
    states = [state("a", 0, 1200, "甲"), state("b", 1200, 2300, "甲"),
              state("c", 2300, 2600, "乙"), state("d", 2600, 5100, "")]
    speech = [{"start_ms": 500, "end_ms": 1000}, {"start_ms": 900, "end_ms": 1700},
              {"start_ms": 3000, "end_ms": 3400}]
    result = derive(states, speech, 2000, "run", "vad")
    assert [(item["duration_ms"], item["blank_screen"], item["source_speech_overlap_ms"])
            for item in result["output_gap_candidates"]] == [(2300, False, 1200), (2500, True, 400)]
    assert [item["event_type"] for item in result["target_update_events"]] == ["text_change", "disappearance"]
    assert [item["duration_ms"] for item in result["inter_update_intervals"]] == [2300, 300]
    assert len(result["subtitle_dwell_intervals"]) == 4
    assert len(result["blank_screen_intervals"]) == 1
    assert result["parameters"]["source_alignment"] == "temporal_overlap_only"


def test_gaps_in_observation_do_not_become_blank_events():
    result = derive([state("a", 0, 300, "甲"), state("b", 1000, 1400, "乙")], [], 100)
    assert result["target_update_events"] == []
    assert overlap_ms(0, 2000, [{"start_ms": 100, "end_ms": 200}, {"start_ms": 150, "end_ms": 300}]) == 200
