package com.georgewaller.audiobrain

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
}
