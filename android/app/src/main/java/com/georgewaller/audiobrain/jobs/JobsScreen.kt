package com.georgewaller.audiobrain.jobs

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.data.Job
import com.georgewaller.audiobrain.data.RunDetail
import com.georgewaller.audiobrain.data.RunSummary
import com.georgewaller.audiobrain.data.instant
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbDivider
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbSwitch
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.SettingRow
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.StatusPill
import com.georgewaller.audiobrain.design.StepTrack
import com.georgewaller.audiobrain.duration
import com.georgewaller.audiobrain.relative
import java.time.Duration

@Composable
fun JobsScreen(vm: JobsViewModel, contentPadding: PaddingValues, onMessage: (String) -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.poll() }
    val response = state.jobs

    LazyColumn(Modifier.fillMaxSize(), contentPadding = contentPadding) {
        item(key = "header") {
            Column(Modifier.padding(top = Ab.space.l, bottom = Ab.space.s)) {
                Overline("On the server")
                Spacer(Modifier.height(Ab.space.xs))
                AbText("Jobs", style = Ab.type.display)
            }
        }
        if (response == null) {
            item(key = "empty") {
                if (state.loading) Row(Modifier.padding(vertical = Ab.space.xl)) { Spinner() }
                else EmptyState("Can't see the jobs", state.error ?: "Connect to the server in Settings.")
            }
            return@LazyColumn
        }
        item(key = "worker") { WorkerBanner(response.worker.alive, response.worker.scheduling, response.worker.host, instant(response.worker.seenAt)) }
        // a lane without a worker is the banner above's to say
        val warnings = state.warnings.filterNot { it.key.startsWith("lane:") }
        if (warnings.isNotEmpty()) item(key = "warnings") { WarningsBanner(warnings.map { it.text }) }
        if (state.error != null) item(key = "error") {
            AbText("Last refresh failed: ${state.error}", style = Ab.type.caption, color = Ab.colors.warning, modifier = Modifier.padding(top = Ab.space.s))
        }
        item(key = "list-header") { SectionHeader("${response.jobs.size} jobs") }
        items(response.jobs, key = { it.key }) { job ->
            JobCard(
                job, expanded = state.expanded == job.key, detail = state.detail?.takeIf { state.expanded == job.key },
                starting = job.key in state.starting, options = state.options[job.key] ?: emptyMap(),
                onToggle = { vm.toggle(job.key) }, onRun = { vm.run(job.key, onMessage) },
                onCancel = { id -> vm.cancel(id, onMessage) }, onOption = { o, on -> vm.setOption(job.key, o, on) },
                modifier = Modifier.animateItem(),
            )
        }
    }
}

/** What the server warns of: models out of date, a scheduled job whose latest run failed. */
@Composable
private fun WarningsBanner(warnings: List<String>) {
    AbSurface(Modifier.fillMaxWidth().padding(top = Ab.space.s), color = Ab.colors.warning.copy(alpha = 0.10f), shape = Ab.shapes.medium) {
        Row(Modifier.padding(Ab.space.m), verticalAlignment = Alignment.CenterVertically) {
            StatusPill("warning", Ab.colors.warning, live = false)
            Spacer(Modifier.width(Ab.space.m))
            Column {
                AbText(if (warnings.size == 1) "Needs a look" else "${warnings.size} things need a look", style = Ab.type.label)
                warnings.forEach { AbText(it, style = Ab.type.caption, color = Ab.colors.textMuted) }
            }
        }
    }
}

@Composable
private fun WorkerBanner(alive: Boolean, scheduling: Boolean, host: String, seen: java.time.Instant?) {
    val (color, title, body) = when {
        !alive -> Triple(Ab.colors.warning, "Worker isn't running", "Runs you start wait in the queue until `manage.py run_jobs` is running on the server${seen?.let { " (last seen ${relative(it)})" } ?: ""}.")
        !scheduling -> Triple(Ab.colors.notNow, "Worker running, schedules off", "It runs what you start here; nothing starts on its own. ($host)")
        else -> Triple(Ab.colors.keep, "Worker running", "Schedules are on. ($host)")
    }
    AbSurface(Modifier.fillMaxWidth().padding(top = Ab.space.s), color = color.copy(alpha = 0.10f), shape = Ab.shapes.medium) {
        Row(Modifier.padding(Ab.space.m), verticalAlignment = Alignment.CenterVertically) {
            StatusPill(if (alive) "live" else "stopped", color, live = alive)
            Spacer(Modifier.width(Ab.space.m))
            Column {
                AbText(title, style = Ab.type.label)
                AbText(body, style = Ab.type.caption, color = Ab.colors.textMuted)
            }
        }
    }
}

@Composable
private fun JobCard(
    job: Job,
    expanded: Boolean,
    detail: RunDetail?,
    starting: Boolean,
    options: Map<String, Boolean>,
    onToggle: () -> Unit,
    onRun: () -> Unit,
    onCancel: (Long) -> Unit,
    onOption: (String, Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val active = job.activeRun
    val last = job.lastRun
    AbSurface(modifier.fillMaxWidth().padding(bottom = Ab.space.s).animateContentSize(), shape = Ab.shapes.large) {
        Column(Modifier.clickable(onClick = onToggle).padding(Ab.space.l)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    AbText(job.title, style = Ab.type.heading)
                    AbText(job.schedule?.let { s -> "$s · next ${relative(instant(job.nextDue))}" } ?: "Only when you ask", style = Ab.type.caption, color = Ab.colors.textMuted)
                }
                Spacer(Modifier.width(Ab.space.s))
                RunPill(active ?: last)
            }
            if (active != null) {
                Spacer(Modifier.height(Ab.space.m))
                StepTrack(active.stepsTotal, active.stepsDone, active.stepsFailed, running = active.state == "running")
                Spacer(Modifier.height(Ab.space.xs))
                AbText(
                    if (active.state == "queued") "Queued ${relative(instant(active.queuedAt))} · waiting for the worker"
                    else "${active.currentStep ?: "Starting"} · ${active.stepsDone}/${active.stepsTotal}",
                    style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1,
                )
            } else if (last != null) {
                Spacer(Modifier.height(Ab.space.s))
                AbText(lastLine(last), style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1)
            }
            AnimatedVisibility(expanded, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
                Column {
                    Spacer(Modifier.height(Ab.space.m))
                    AbText(job.description, style = Ab.type.body, color = Ab.colors.textMuted)
                    job.options.forEach { (name, description) ->
                        SettingRow(name.replace('_', ' ').replaceFirstChar { it.uppercase() }, description = description) {
                            AbSwitch(options[name] == true, { onOption(name, it) })
                        }
                    }
                    Spacer(Modifier.height(Ab.space.m))
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        if (active?.state == "queued") AbButton("Cancel", { onCancel(active.id) }, kind = ButtonKind.Ghost, icon = AbIcons.Stop)
                        AbButton(
                            if (starting) "Starting…" else if (active != null) "Running" else "Run now", onRun,
                            icon = AbIcons.Play, enabled = !starting && active == null,
                        )
                    }
                    if (detail != null) RunDetailView(detail)
                }
            }
        }
    }
}

@Composable
private fun RunPill(run: RunSummary?) {
    when (run?.state) {
        null -> StatusPill("never run", Ab.colors.textFaint)
        "running" -> StatusPill("running", Ab.colors.accent, live = true)
        "queued" -> StatusPill("queued", Ab.colors.notNow)
        "failed" -> StatusPill(if (run.stepsFailed > 0) "${run.stepsFailed} failed" else "failed", Ab.colors.no)
        "succeeded" -> StatusPill("ok", Ab.colors.keep)
        else -> StatusPill(run.state, Ab.colors.textFaint)
    }
}

private fun lastLine(run: RunSummary): String {
    val finished = instant(run.finishedAt)
    val took = instant(run.startedAt)?.let { s -> finished?.let { duration(Duration.between(s, it).seconds) } }
    val by = when (run.trigger) { "app" -> "from the app"; "cli" -> "from the command line"; else -> "on schedule" }
    return listOfNotNull("${if (run.state == "succeeded") "Succeeded" else "Finished with failures"} ${relative(finished)}", took?.let { "took $it" }, by).joinToString(" · ")
}

@Composable
private fun RunDetailView(run: RunDetail) {
    Column(Modifier.padding(top = Ab.space.l)) {
        AbDivider()
        SectionHeader("Run ${run.id} · ${run.state}")
        run.steps.forEach { step ->
            Row(Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
                val (mark, color) = when (step.state) {
                    "ok" -> "✓" to Ab.colors.keep
                    "failed" -> "✗" to Ab.colors.no
                    "running" -> "›" to Ab.colors.accent
                    else -> "·" to Ab.colors.textFaint
                }
                AbText(mark, style = Ab.type.number, color = color, modifier = Modifier.width(18.dp))
                Column(Modifier.weight(1f)) {
                    AbText(step.name, style = Ab.type.label, color = if (step.state == "pending") Ab.colors.textFaint else Ab.colors.text)
                    step.result?.let { AbText(it, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 3) }
                }
                step.seconds?.let { AbText(duration(it.toLong()), style = Ab.type.number, color = Ab.colors.textFaint) }
            }
        }
        if (run.log.isNotBlank()) {
            SectionHeader("Log")
            AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.ground, shape = Ab.shapes.small) {
                SelectionContainer {
                    AbText(
                        run.log.lines().takeLast(40).joinToString("\n"), style = Ab.type.code, color = Ab.colors.textMuted,
                        modifier = Modifier.horizontalScroll(rememberScrollState()).padding(Ab.space.m),
                    )
                }
            }
        }
        if (run.error.isNotBlank()) {
            SectionHeader("First failure")
            AbText(run.error.lines().takeLast(12).joinToString("\n"), style = Ab.type.code, color = Ab.colors.no)
        }
    }
}
