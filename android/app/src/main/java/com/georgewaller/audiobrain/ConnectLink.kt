package com.georgewaller.audiobrain

import android.net.Uri

/**
 * The server and token from an audiobrain://connect?url=…&token=… link (printed by `manage.py api_token`), found
 * anywhere in [text], so a pasted note or message works too. Most apps don't make custom-scheme links tappable,
 * so Settings also takes the link pasted in.
 */
fun parseConnectLink(text: String): Pair<String, String>? {
    val link = Regex("""audiobrain://connect\?\S+""").find(text)?.value ?: return null
    val uri = Uri.parse(link)
    val url = uri.getQueryParameter("url").orEmpty()
    val token = uri.getQueryParameter("token").orEmpty()
    return if (url.isBlank() || token.isBlank()) null else url to token
}
