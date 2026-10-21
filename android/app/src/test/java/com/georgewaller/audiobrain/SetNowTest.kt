package com.georgewaller.audiobrain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SetNowTest {
    @Test
    fun cueTimes() {
        assertEquals(3750L, SetNow.cueSeconds("1:02:30"))
        assertEquals(3750L, SetNow.cueSeconds("62:30"))
        assertEquals(250L, SetNow.cueSeconds("[04:10]"))
        assertEquals(0L, SetNow.cueSeconds("0:00"))
    }

    @Test
    fun trackNumbersAreNotTimes() {
        assertNull(SetNow.cueSeconds("043"))
        assertNull(SetNow.cueSeconds("0??"))
        assertNull(SetNow.cueSeconds(""))
    }
}
