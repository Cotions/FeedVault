"""Full-size instaloader metadata, for the slim form's tests (test_slim.py).

Shapes follow what instaloader 4.x writes: Instagram's GraphQL node with,
since 4.10, the iPhone API's answer for the same post under
``iphone_struct`` (every resolution's CDN URL, the owner again, the
carousel's items again), which is most of a file. Handles, names, captions
and ids are invented; the URLs lead nowhere. Each builder returns the whole
structure, ``{"node": {...}, "instaloader": {...}}``.
"""
import hashlib

CAPTION = ("Slow morning at the market #Tomatoes #basil\n\nEverything on this table came from within "
           "ten kilometres. Recipe in the comments, as promised last week. " * 4).strip()


def _url(seed, w=1080, h=1350, ext="jpg"):
    """A CDN URL as long as Instagram's signed ones."""
    sig = hashlib.sha256(f"{seed}:{w}x{h}".encode()).hexdigest()
    return (f"https://scontent-cdg4-2.cdninstagram.com/v/t51.29350-15/{sig[:24]}_{sig[24:40]}_n.{ext}"
            f"?stp=dst-jpg_e35_p{w}x{h}_sh0.08&_nc_ht=scontent-cdg4-2.cdninstagram.com&_nc_cat=100&_nc_oc={sig}"
            f"&_nc_ohc={sig[:22]}&_nc_gid={sig[10:30]}&edm=APs17CUBAAAA&ccb=7-5&oh=00_{sig[:40]}&oe=66{sig[:6].upper()}"
            f"&_nc_sid=10d13b")


def _user(owner, seed="u"):
    return {"pk": int(owner["id"]), "pk_id": owner["id"], "id": owner["id"], "username": owner["username"],
            "full_name": owner.get("full_name", ""), "is_private": False, "is_verified": False,
            "profile_pic_id": f"{owner['id']}_3300000000000000000", "profile_pic_url": _url(seed, 150, 150),
            "hd_profile_pic_url_info": {"url": _url(seed, 1080, 1080), "width": 1080, "height": 1080},
            "friendship_status": {"following": True, "is_bestie": False, "is_restricted": False, "is_feed_favorite": False},
            "account_badges": [], "fan_club_info": {"fan_club_id": None, "fan_club_name": None}, "transparency_product_enabled": False}


def _candidates(seed):
    sizes = [(1080, 1350), (828, 1035), (750, 937), (640, 800), (480, 600), (320, 400), (240, 300),
             (1080, 1080), (750, 750), (640, 640), (480, 480), (320, 320), (240, 240), (150, 150)]
    return {"candidates": [{"width": w, "height": h, "url": _url(seed, w, h),
                            "scans_profile": "e35", "estimated_scans_sizes": [9000 + w, 18000 + w, 27000 + w]}
                           for w, h in sizes],
            "additional_candidates": {"igtv_first_frame": {"width": 1080, "height": 1350, "url": _url(seed + "f")},
                                      "first_frame": {"width": 1080, "height": 1350, "url": _url(seed + "g")},
                                      "smart_frame": None},
            "smart_thumbnail_enabled": False}


def _videos(seed):
    return [{"type": t, "width": w, "height": h, "url": _url(f"{seed}v{t}", w, h, "mp4"), "id": f"{seed}{t}"}
            for t, (w, h) in ((101, (720, 1280)), (102, (480, 854)), (103, (480, 854)))]


def _iphone_item(seed, owner, ts, video=False, carousel=None):
    item = {"taken_at": ts, "pk": int(hashlib.sha1(seed.encode()).hexdigest()[:15], 16), "id": f"{seed}_{owner['id']}",
            "device_timestamp": ts * 1000000, "client_cache_key": hashlib.md5(seed.encode()).hexdigest(),
            "filter_type": 0, "caption_is_edited": False, "like_and_view_counts_disabled": False,
            "is_reshare_of_text_post_app_media_in_ig": False, "is_post_live_clips_media": False,
            "deleted_reason": 0, "integrity_review_decision": "pending", "has_shared_to_fb": 0,
            "is_unified_video": False, "should_request_ads": False, "is_visual_reply_commenter_notice_enabled": True,
            "commerciality_status": "not_commercial", "explore_hide_comments": False, "has_delayed_metadata": False,
            "location": None, "lat": None, "lng": None, "is_organic_product_tagging_eligible": False,
            "can_see_insights_as_brand": False, "media_type": 8 if carousel else 2 if video else 1,
            "code": seed, "can_viewer_reshare": True, "caption": None, "clips_tab_pinned_user_ids": [],
            "comment_inform_treatment": {"should_have_inform_treatment": False, "text": "", "url": None,
                                         "action_type": None},
            "sharing_friction_info": {"should_have_sharing_friction": False, "bloks_app_url": None,
                                      "sharing_friction_payload": None},
            "play_count": None, "original_media_has_visual_reply_media": False, "like_count": 0,
            "fb_like_count": 0, "can_viewer_save": True, "is_in_profile_grid": False, "profile_grid_control_enabled": False,
            "organic_tracking_token": hashlib.sha256(seed.encode()).hexdigest() * 2,
            "has_liked": False, "comment_count": 0, "photo_of_you": False, "is_comments_gif_composer_enabled": True,
            "product_type": "carousel_container" if carousel else "clips" if video else "feed",
            "comment_threading_enabled": True, "max_num_visible_preview_comments": 2,
            "has_more_comments": True, "preview_comments": [
                {"pk": f"1800000000000{i}", "user_id": 900 + i, "type": 0, "did_report_as_spam": False,
                 "created_at": ts + 60 * i, "created_at_utc": ts + 60 * i, "content_type": "comment", "status": "Active",
                 "bit_flags": 0, "share_enabled": False, "is_ranked_comment": True, "media_id": 1,
                 "user": _user({"id": str(900 + i), "username": f"friend_{i}", "full_name": f"Friend {i}"}, f"c{i}"),
                 "text": f"so good ({i})", "is_covered": False, "has_liked_comment": False, "comment_like_count": i,
                 "private_reply_status": 0} for i in range(2)],
            "comments": [], "top_likers": [], "facepile_top_likers": [], "likers": [],
            "user": _user(owner, seed), "owner": _user(owner, seed), "can_view_more_preview_comments": True,
            "hide_view_all_comment_entrypoint": False, "inline_composer_display_condition": "impression_trigger",
            "music_metadata": {"music_canonical_id": "0", "audio_type": None, "music_info": None,
                               "original_sound_info": None, "pinned_media_ids": None}}
    if carousel:
        item["carousel_media_count"] = len(carousel)
        item["carousel_media"] = [
            {"id": f"{seed}{i}_{owner['id']}", "media_type": 2 if v else 1, "pk": 3300000000000000000 + i,
             "carousel_parent_id": item["id"], "original_width": 1080, "original_height": 1350,
             "image_versions2": _candidates(f"{seed}{i}"), "commerciality_status": "not_commercial",
             "sharing_friction_info": {"should_have_sharing_friction": False, "bloks_app_url": None},
             "product_type": "carousel_item", "usertags": {"in": []}, "accessibility_caption": "Photo of a market stall.",
             **({"video_versions": _videos(f"{seed}{i}"), "video_duration": 12.5, "video_codec": "avc1.64001F"} if v else {})}
            for i, v in enumerate(carousel, 1)]
    else:
        item["image_versions2"] = _candidates(seed)
        item["original_width"], item["original_height"] = 1080, 1350
        if video:
            item.update(video_versions=_videos(seed), video_duration=31.7, has_audio=True, view_count=None,
                        video_codec="avc1.64001F", number_of_qualities=3,
                        video_dash_manifest="<MPD>" + "".join(f'<Representation id="{i}" bandwidth="{90000 * i}">'
                                                             f"<BaseURL>{_url(seed + str(i), 720, 1280, 'mp4')}</BaseURL>"
                                                             "</Representation>" for i in range(1, 9)) + "</MPD>",
                        clips_metadata={"music_info": None, "original_sound_info": {
                            "audio_asset_id": 77, "progressive_download_url": _url(seed + "a", 0, 0, "m4a"),
                            "dash_manifest": "<MPD/>", "ig_artist": _user(owner, seed + "s"), "duration_in_ms": 31700,
                            "original_audio_title": "Original audio"}, "audio_type": "original_sounds"})
    return item


def _resources(seed):
    return [{"src": _url(seed, w, h), "config_width": w, "config_height": h}
            for w, h in ((640, 800), (750, 937), (1080, 1350))]


def _graph_media(seed, owner, ts, typename, video=False):
    return {"__typename": typename, "id": str(int(hashlib.sha1(seed.encode()).hexdigest()[:15], 16)), "shortcode": seed,
            "dimensions": {"height": 1350, "width": 1080}, "gating_info": None, "fact_check_overall_rating": None,
            "fact_check_information": None, "sensitivity_friction_info": None, "sharing_friction_info": {
                "should_have_sharing_friction": False, "bloks_app_url": None},
            "media_overlay_info": None, "media_preview": "ACEq" + hashlib.sha256(seed.encode()).hexdigest() * 6,
            "display_url": _url(seed), "display_resources": _resources(seed),
            "accessibility_caption": "Photo by Carol on June 01, 2024. May be an image of food.",
            "is_video": video, "tracking_token": hashlib.sha256((seed + "t").encode()).hexdigest() * 2,
            "upcoming_event": None, "edge_media_to_tagged_user": {"edges": []},
            **({"video_url": _url(seed, 720, 1280, "mp4"), "has_audio": True, "video_duration": 31.7,
                "dash_info": {"is_dash_eligible": True, "video_dash_manifest": "<MPD/>", "number_of_qualities": 3},
                "thumbnail_src": _url(seed + "th", 640, 640)} if video else {})}


def post(shortcode, ts, owner, typename="GraphImage", caption=CAPTION, slides=None, tagged=(), location=None,
         likes=1234, comments=56, views=None, product_type=None, iphone=True):
    """A Post as instaloader 4.10+ writes it: the GraphQL node, with the
    iPhone API's answer inside. ``slides``: a carousel's, True for a video;
    ``tagged``: owners (as fakes.owner) tagged in it."""
    video = typename == "GraphVideo"
    node = _graph_media(shortcode, owner, ts, typename, video)
    node.update({
        "taken_at_timestamp": ts, "comments_disabled": False, "commenting_disabled_for_viewer": False,
        "viewer_has_liked": False, "viewer_has_saved": False, "viewer_has_saved_to_collection": False,
        "viewer_in_photo_of_you": False, "viewer_can_reshare": True, "is_affiliate": False, "is_paid_partnership": False,
        "is_ad": False, "caption_is_edited": False, "has_ranked_comments": False, "like_and_view_counts_disabled": False,
        "title": "", "pinned_for_users": [], "nft_asset_info": None, "coauthor_producers": [],
        "edge_media_to_caption": {"edges": [{"node": {"created_at": str(ts), "text": caption, "id": "180"}}]
                                  if caption else []},
        "edge_media_to_comment": {"count": comments, "page_info": {"has_next_page": True, "end_cursor": "QVFE" * 30},
                                  "edges": [{"node": {"id": f"179{i}", "text": f"comment {i}", "created_at": ts + i,
                                                      "did_report_as_spam": False, "viewer_has_liked": False,
                                                      "owner": {"id": str(800 + i), "is_verified": False,
                                                                "profile_pic_url": _url(f"cm{i}", 150, 150),
                                                                "username": f"reader_{i}"},
                                                      "edge_liked_by": {"count": i}, "is_restricted_pending": False}}
                                            for i in range(12)]},
        "edge_media_preview_comment": {"count": comments, "edges": []},
        "edge_media_preview_like": {"count": likes, "edges": []},
        "edge_liked_by": {"count": likes},
        "edge_media_to_sponsor_user": {"edges": []},
        "edge_web_media_to_related_media": {"edges": []},
        "edge_related_profiles": {"edges": []},
        "owner": {"id": owner["id"], "username": owner["username"], "full_name": owner.get("full_name", ""),
                  "is_verified": False, "is_private": False, "profile_pic_url": _url("pp" + owner["id"], 150, 150),
                  "blocked_by_viewer": False, "restricted_by_viewer": None, "followed_by_viewer": True,
                  "has_blocked_viewer": False, "is_embeds_disabled": False, "is_unpublished": False,
                  "requested_by_viewer": False, "pass_tiering_recommendation": True,
                  "edge_owner_to_timeline_media": {"count": 412}, "edge_followed_by": {"count": 9876}},
        "location": {"id": "213385402", "has_public_page": True, "name": location, "slug": "somewhere",
                     "address_json": '{"street_address": "", "city_name": "Somewhere"}'} if location else None,
        "thumbnail_src": _url(shortcode + "t", 640, 640),
        "thumbnail_resources": [{"src": _url(shortcode + "t", s, s), "config_width": s, "config_height": s}
                                for s in (150, 240, 320, 480, 640)],
    })
    if tagged:
        node["edge_media_to_tagged_user"] = {"edges": [
            {"node": {"user": {"full_name": t.get("full_name", ""), "followed_by_viewer": False, "id": t["id"],
                               "is_verified": False, "profile_pic_url": _url("tg" + t["id"], 150, 150),
                               "username": t["username"]}, "x": 0.31, "y": 0.62}} for t in tagged]}
    if video:
        node.update(video_view_count=views, video_play_count=None, product_type=product_type or "feed",
                    clips_music_attribution_info=None, encoding_status=None, is_published=True)
    elif product_type:
        node["product_type"] = product_type
    if slides:
        node["edge_sidecar_to_children"] = {"edges": [
            {"node": _graph_media(f"{shortcode}{i}", owner, ts, "GraphVideo" if v else "GraphImage", v)}
            for i, v in enumerate(slides, 1)]}
    if iphone:
        item = _iphone_item(shortcode, owner, ts, video, slides)
        item.update(like_count=likes, comment_count=comments, play_count=views,
                    caption={"pk": "180", "user_id": int(owner["id"]), "text": caption, "type": 1, "created_at": ts,
                             "created_at_utc": ts, "content_type": "comment", "status": "Active", "bit_flags": 0,
                             "did_report_as_spam": False, "share_enabled": False, "user": _user(owner, "cap"),
                             "is_covered": False, "is_ranked_comment": False, "media_id": item["pk"],
                             "private_reply_status": 0} if caption else None)
        node["iphone_struct"] = item
    return {"node": node, "instaloader": {"version": "4.13.1", "node_type": "Post"}}


def story_item(item_id, ts, owner, video=False):
    """A StoryItem (a story's or a highlight's item)."""
    seed = f"st{item_id}"
    node = {"__typename": "GraphStoryVideo" if video else "GraphStoryImage", "id": str(item_id),
            "audience": "MediaAudience.DEFAULT", "dimensions": {"height": 1920, "width": 1080},
            "display_resources": _resources(seed), "display_url": _url(seed, 1080, 1920),
            "edge_story_media_viewers": {"count": 0, "edges": [], "page_info": {"has_next_page": False, "end_cursor": None}},
            "expiring_at_timestamp": ts + 86400, "fact_check_information": None, "fact_check_overall_rating": None,
            "gating_info": None, "is_video": video, "media_preview": "ACEq" + hashlib.sha256(seed.encode()).hexdigest() * 4,
            "overlay_image_resources": None, "owner": {"id": owner["id"], "username": owner["username"],
                                                       "full_name": owner.get("full_name", ""), "is_private": False,
                                                       "profile_pic_url": _url("pp" + owner["id"], 150, 150),
                                                       "followed_by_viewer": True, "requested_by_viewer": False},
            "sensitivity_friction_info": None, "sharing_friction_info": {"should_have_sharing_friction": False,
                                                                         "bloks_app_url": None},
            "should_log_client_event": False, "show_one_tap_fb_share_tooltip": False, "story_app_attribution": None,
            "story_cta_url": "https://example.invalid/shop", "story_view_count": None,
            "taken_at_timestamp": ts, "tappable_objects": [
                {"__typename": "GraphTappableMention", "x": 0.5, "y": 0.8, "width": 0.4, "height": 0.05, "rotation": 0.0,
                 "custom_title": None, "attribution": None, "username": "friend_1", "full_name": "Friend 1",
                 "is_private": False}],
            "tracking_token": hashlib.sha256((seed + "t").encode()).hexdigest() * 2,
            **({"video_duration": 14.9, "video_resources": [
                {"src": _url(seed + str(i), 720, 1280, "mp4"), "config_width": 720, "config_height": 1280,
                 "mime_type": 'video/mp4; codecs="avc1.64001F, mp4a.40.2"', "profile": "MAIN"} for i in range(2)],
                "has_audio": True} if video else {}),
            "iphone_struct": {**_iphone_item(seed, owner, ts, video), "expiring_at": ts + 86400, "story_feed_media": [],
                              "can_reply": True, "can_reshare": True, "supports_reel_reactions": True}}
    return {"node": node, "instaloader": {"version": "4.13.1", "node_type": "StoryItem"}}


def profile(owner, bio="Cook. Market regular.\nlinktr.ee/carol.cooks", url="https://carol.example/",
            links=("https://carol.example/book", "https://shop.example/carol")):
    """A Profile file (``<handle>_<id>.json.xz``)."""
    seed = "pr" + owner["id"]
    node = {"id": owner["id"], "username": owner["username"], "full_name": owner.get("full_name", ""),
            "biography": bio, "biography_with_entities": {"raw_text": bio, "entities": []},
            "bio_links": [{"title": f"Link {i}", "lynx_url": "https://l.instagram.com/?u=" + u, "url": u, "link_type": "external"}
                          for i, u in enumerate(links)],
            "external_url": url, "external_url_linkshimmed": "https://l.instagram.com/?u=" + (url or ""),
            "blocked_by_viewer": False, "restricted_by_viewer": False, "country_block": False, "eimu_id": "1170000",
            "edge_followed_by": {"count": 9876}, "edge_follow": {"count": 321}, "fbid": "17841400000000000",
            "followed_by_viewer": True, "follows_viewer": False, "has_ar_effects": False, "has_clips": True,
            "has_guides": False, "has_channel": False, "has_blocked_viewer": False, "highlight_reel_count": 7,
            "has_requested_viewer": False, "hide_like_and_view_counts": False, "is_business_account": True,
            "is_professional_account": True, "is_supervision_enabled": False, "is_joined_recently": False,
            "business_category_name": "Creators & Celebrities", "category_name": "Chef", "is_private": False,
            "is_verified": False, "pronouns": [], "profile_pic_url": _url(seed, 150, 150),
            "profile_pic_url_hd": _url(seed, 320, 320), "requested_by_viewer": False, "should_show_category": True,
            "edge_mutual_followed_by": {"count": 2, "edges": [{"node": {"username": "friend_1"}}]},
            "edge_felix_video_timeline": {"count": 0, "page_info": {"has_next_page": False, "end_cursor": None}, "edges": []},
            "edge_owner_to_timeline_media": {"count": 412, "page_info": {"has_next_page": True, "end_cursor": "QVFE" * 30},
                                             "edges": [{"node": _graph_media(f"{seed}{i}", owner, 1717243200 + i, "GraphImage")}
                                                       for i in range(12)]},
            "edge_saved_media": {"count": 0, "page_info": {"has_next_page": False, "end_cursor": None}, "edges": []},
            "edge_media_collections": {"count": 0, "page_info": {"has_next_page": False, "end_cursor": None}, "edges": []},
            "edge_related_profiles": {"edges": [{"node": {"id": str(700 + i), "full_name": f"Other {i}", "is_private": False,
                                                          "is_verified": False, "profile_pic_url": _url(f"rp{i}", 150, 150),
                                                          "username": f"other_{i}"}} for i in range(30)]},
            "iphone_struct": {**_user(owner, seed), "biography": bio, "media_count": 412, "follower_count": 9876,
                              "following_count": 321, "external_url": url,
                              "hd_profile_pic_versions": [{"width": w, "height": w, "url": _url(seed, w, w)}
                                                          for w in (320, 640)]}}
    return {"node": node, "instaloader": {"version": "4.13.1", "node_type": "Profile"}}


def old_post(shortcode, ts, owner, caption="An old one #tbt"):
    """A Post as instaloader 4.0 to 4.9 wrote it: the GraphQL node alone,
    ``date`` and ``edge_media_to_caption`` on some, ``caption`` as plain
    text and ``code`` on the oldest."""
    seed = "old" + shortcode
    return {"node": {"__typename": "GraphImage", "id": str(int(hashlib.sha1(seed.encode()).hexdigest()[:15], 16)),
                     "code": shortcode, "date": ts, "caption": caption, "comments_disabled": False,
                     "dimensions": {"height": 1080, "width": 1080}, "display_src": _url(seed, 1080, 1080),
                     "display_url": _url(seed, 1080, 1080), "is_video": False,
                     "likes": {"count": 42}, "comments": {"count": 3},
                     "edge_liked_by": {"count": 42}, "edge_media_to_comment": {"count": 3},
                     "owner": {"id": owner["id"]}, "thumbnail_src": _url(seed + "t", 640, 640),
                     "thumbnail_resources": [{"src": _url(seed + "t", s, s), "config_width": s, "config_height": s}
                                             for s in (150, 240, 320, 480, 640)],
                     "media_preview": "ACEq" + hashlib.sha256(seed.encode()).hexdigest() * 6},
            "instaloader": {"version": "4.2.5", "node_type": "Post"}}


def iphone_only(shortcode, ts, owner, caption=CAPTION, video=True):
    """A Post whose node holds little beside ``iphone_struct`` (Instagram's
    XDT nodes, 2024): the owner's names, the caption, the date and the
    counts are only in there."""
    item = _iphone_item(shortcode, owner, ts, video)
    item.update(like_count=77, comment_count=4, play_count=5300, view_count=None,
                caption={"pk": "181", "text": caption, "created_at": ts, "user": _user(owner, "cap")})
    return {"node": {"__typename": "XDTGraphVideo" if video else "XDTGraphImage", "code": shortcode,
                     "pk": str(item["pk"]), "id": item["id"], "owner": {"pk": owner["id"]},
                     "is_video": video, "iphone_struct": item},
            "instaloader": {"version": "4.14.1", "node_type": "Post"}}
