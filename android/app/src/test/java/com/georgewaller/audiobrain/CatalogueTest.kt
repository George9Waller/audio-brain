package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.SetRow
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.AgeTone
import com.georgewaller.audiobrain.overlays.released
import com.georgewaller.audiobrain.overlays.yearsLine
import com.georgewaller.audiobrain.pages.Lens
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class CatalogueTest {
    @Test
    fun artistsAreNowWhenThisYearIsHalfTheirBiggest() {
        // Becky Hill: 238 at her peak, 156 this year
        assertEquals(AgeTag("Now", AgeTone.Now), artistTag(mapOf("2022" to 238, "2024" to 238, "2026" to 156), now = 2026))
        // Einaudi: 2,517 in 2018, 10 this year
        assertEquals(AgeTag("2018", AgeTone.Era), artistTag(mapOf("2018" to 2517, "2019" to 617, "2026" to 10), now = 2026))
        assertNull(artistTag(emptyMap(), now = 2026))
    }

    @Test
    fun tracksAreTaggedByWhenTheyWereLastPlayed() {
        fun item(age: String, peak: String? = null) = ListItem(kind = "track", trackId = 1, title = "t", age = age, peak = peak)
        assertEquals(AgeTag("Now", AgeTone.Now), item("lately").ageTag())
        assertEquals(AgeTag("Lately", AgeTone.Lately), item("year").ageTag())
        assertEquals(AgeTag("2016", AgeTone.Era), item("older", "2016-03").ageTag())
        assertEquals(AgeTag("New", AgeTone.New), item("never").ageTag())
    }

    @Test
    fun albumsAndSetsAreTaggedByTheirPlays() {
        val now = LocalDate.of(2026, 10, 31)
        assertEquals(AgeTone.Now, playedTag(3, "2026-10-20T10:00:00Z", now)?.tone)
        assertEquals(AgeTone.Lately, playedTag(0, "2026-03-01T10:00:00Z", now)?.tone)
        assertEquals(AgeTag("2019", AgeTone.Era), playedTag(0, "2019-03-01T10:00:00Z", now))
        assertEquals(AgeTone.New, playedTag(0, null, now)?.tone)
    }

    @Test
    fun setRowsSayHowLongWhenAndWhere() {
        val played = SetRow(1, "Meduza", "Meduza", "played", minutes = 98, plays = 10, lastPlayed = "2026-10-20T20:00:00Z", url = "")
        assertTrue(setMeta(played).startsWith("98 min · 10 plays · last 20 Oct"))
        val saved = SetRow(2, "Jamie xx", "ARTE", "saved", minutes = 109, savedAt = "2026-10-26T20:00:00Z", url = "")
        assertEquals("109 min · saved 26 Oct · not played", setMeta(saved))
        val new = SetRow(3, "ANOTR", "Mayan Warrior", "new", minutes = 116, published = "2025-10-01", url = "")
        assertEquals("116 min · 1 Oct 2025 · Mayan Warrior", setMeta(new))
    }

    @Test
    fun theMapLensesFilterByRecency() {
        val lately = 2 or 32
        val thisYear = 2 or 64
        val faded = 2
        val never = 16
        assertTrue(Lens.Lately.shows(lately)); assertFalse(Lens.Lately.shows(thisYear))
        assertTrue(Lens.Faded.shows(faded)); assertFalse(Lens.Faded.shows(thisYear)); assertFalse(Lens.Faded.shows(never))
        assertTrue(Lens.Unplayed.shows(never)); assertFalse(Lens.Unplayed.shows(faded))
        assertTrue(listOf(lately, thisYear, faded, never).all { Lens.All.shows(it) })
    }

    @Test
    fun wordsForPlaysOverTime() {
        // CamelPhat this year: almost all in October
        assertEquals("October", sinceMonth(listOf(0, 1, 0, 0, 0, 0, 0, 0, 0, 22)))
        assertEquals("August", sinceMonth(listOf(2, 2, 2, 2, 2, 2, 2, 20, 10, 10)))
        assertNull(sinceMonth(listOf(5, 0, 0)))
        assertEquals("Biggest in 2022 and 2024 (238 plays each); 156 so far this year.", yearsLine(mapOf("2022" to 238, "2024" to 238, "2026" to 156), now = 2026))
        assertEquals("22 May 2026", released("2026-05-22"))
        assertEquals("2019", released("2019"))
    }
}
