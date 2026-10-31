package com.georgewaller.audiobrain.sets

import android.annotation.SuppressLint
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.georgewaller.audiobrain.data.ApiJson
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.Spinner

/** The page without what the server doesn't read, to keep what's sent small. */
private const val PAGE = """(function () {
  var page = document.documentElement.cloneNode(true);
  page.querySelectorAll('script, style, svg, iframe, noscript, link').forEach(function (e) { e.remove(); });
  return page.outerHTML;
})()"""

/**
 * A link the server was turned away from, opened here so it loads as in any browser (1001Tracklists checks for one
 * first). Once it has, "Use this tracklist" sends the page as shown, with the address it ended up at.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun PageBrowser(url: String, padding: PaddingValues, onPage: (url: String, html: String) -> Unit, onClose: () -> Unit) {
    val context = LocalContext.current
    var loading by remember { mutableStateOf(true) }
    val web = remember {
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) { loading = true }
                override fun onPageFinished(view: WebView, url: String) { loading = false }
            }
            loadUrl(url)
        }
    }
    DisposableEffect(web) { onDispose { web.destroy() } }
    BackHandler { if (web.canGoBack()) web.goBack() else onClose() }
    Column(Modifier.fillMaxSize().background(Ab.colors.ground).padding(padding)) {
        Column(Modifier.fillMaxWidth().padding(vertical = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbText("The server couldn't open this page, so it's opened here. Once the tracklist shows, use it.", style = Ab.type.caption, color = Ab.colors.textMuted)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                AbButton("Use this tracklist", {
                    web.evaluateJavascript(PAGE) { json ->
                        val html = runCatching { ApiJson.decodeFromString<String>(json) }.getOrNull()
                        if (!html.isNullOrBlank()) onPage(web.url ?: url, html)
                    }
                }, enabled = !loading)
                AbButton("Cancel", onClose, kind = ButtonKind.Ghost)
                if (loading) Spinner(size = 16.dp)
            }
        }
        AndroidView({ web }, Modifier.fillMaxWidth().weight(1f))
    }
}
