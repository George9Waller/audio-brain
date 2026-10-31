package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.QueueSummary
import com.georgewaller.audiobrain.data.Then
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.YearMonth

class DiscoverTest {
    @Test
    fun yearBarsLightThePeakAndThisYear() {
        val bars = yearBars(mapOf("2018" to 2517, "2019" to 617, "2026" to 10), to = 2026)
        assertEquals(11, bars.size)
        assertEquals(2018, bars.single { it.peak }.year)
        assertEquals(2026, bars.single { it.now }.year)
        assertEquals(0, bars.first { it.year == 2017 }.plays)
        assertEquals(emptyList<YearBar>(), yearBars(emptyMap(), to = 2026).filter { it.peak })
    }

    @Test
    fun thenLineRunsFromThePeakToTheLastPlay() {
        val (peak, last) = thenPositions(Then(265, 225, "2016-03", "2025-01"), now = YearMonth.of(2026, 1))!!
        assertEquals(2f / 120, peak, 0.001f)
        assertEquals(108f / 120, last, 0.001f)
        assertNull(thenPositions(Then(), now = YearMonth.of(2026, 1)))
        assertEquals("Mar 2016", monthLabel("2016-03"))
    }

    @Test
    fun soundsLikeComesFromTheReason() {
        assertEquals("Paul Woolford - In My Head and Wh0 - Girls & Boys", soundsLike("“Signs” sounds like Paul Woolford - In My Head and Wh0 - Girls & Boys"))
        assertNull(soundsLike("Not analysed yet"))
    }

    private fun releases(n: Int) = (1L..n).map { ListItem(kind = "release", releaseId = it, title = "r$it", state = "new") }

    @Test
    fun decidingOneBringsTheNextUp() {
        val items = releases(5)
        assertEquals(listOf(1L, 2L, 3L), refill(items, emptyMap(), emptySet(), 3).map { it.item.releaseId })
        val after = refill(items, mapOf(2L to "dismissed"), setOf(2L), 3)
        assertEquals(listOf(1L, 2L, 3L, 4L), after.map { it.item.releaseId })
        assertEquals(Slot.Decided(items[1], "dismissed"), after[1])
        assertEquals(true, (after[3] as Slot.Open).fresh)
        assertEquals(false, (after[0] as Slot.Open).fresh)
    }

    @Test
    fun undoPutsItBackAndOldDecisionsStayGone() {
        val items = releases(5)
        // undone on this visit: back in its place, and the one that came up goes again
        assertEquals(listOf(1L, 2L, 3L), refill(items, mapOf(2L to "new"), setOf(2L), 3).map { it.item.releaseId })
        // decided before this visit (or by the server): not shown at all
        assertEquals(listOf(2L, 3L, 4L), refill(items, mapOf(1L to "saved"), emptySet(), 3).map { it.item.releaseId })
        assertEquals(listOf(3L, 4L), refill(items.mapIndexed { i, r -> if (i < 2) r.copy(state = "heard") else r }, emptyMap(), emptySet(), 2).map { it.item.releaseId })
    }

    @Test
    fun countsTakeOffOnlyWhatWasDecidedSinceTheyCame() {
        val summary = QueueSummary(mapOf("artist" to 10, "label" to 5), decided = 3, decidedThisWeek = 1)
        val sources = mapOf(1L to "artist", 2L to "artist", 3L to "label")
        // 1 was decided before the counts came (so they include it); 2 and 3 since; 4 has no known source
        val now = queueNow(summary, mapOf(1L to "saved"), mapOf(1L to "saved", 2L to "dismissed", 3L to "saved", 4L to "dismissed"), sources)
        assertEquals(mapOf("artist" to 9, "label" to 4), now.waiting)
        assertEquals(6, now.decided)
        // undoing one the counts had as decided puts it back
        assertEquals(11, queueNow(summary, mapOf(1L to "saved"), mapOf(1L to "new"), sources).waiting["artist"])
    }
}
