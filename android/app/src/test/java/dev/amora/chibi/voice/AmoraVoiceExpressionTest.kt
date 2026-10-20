package dev.amora.chibi.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AmoraVoiceExpressionTest {
    @Test
    fun neutralVadPreservesDelivery() {
        val plan = AmoraVoiceExpressionPlanner.fromVad(0f, 0f, 0f)
        assertEquals("NEUTRA", plan.label)
        assertEquals(1f, plan.rateScale, 0.0001f)
        assertEquals(1f, plan.energyScale, 0.0001f)
        assertEquals(1f, plan.f0RegisterScale, 0.0001f)
    }

    @Test
    fun sadPresetIsSlowerLowerAndQuieter() {
        val plan = AmoraVoiceExpressionPlanner.preset("TRISTE")
        assertTrue(plan.rateScale < 1f)
        assertTrue(plan.energyScale < 1f)
        assertTrue(plan.f0RegisterScale < 1f)
        assertTrue(plan.pauseScale > 1f)
    }

    @Test
    fun tenderAndSadRemainDifferent() {
        val tender = AmoraVoiceExpressionPlanner.preset("CARINHOSA")
        val sad = AmoraVoiceExpressionPlanner.preset("TRISTE")
        assertTrue(tender.softness > sad.softness)
        assertTrue(tender.energyScale > sad.energyScale)
        assertTrue(tender.f0RegisterScale > sad.f0RegisterScale)
    }

    @Test
    fun angryPresetIsFirmAndBounded() {
        val angry = AmoraVoiceExpressionPlanner.preset("BRAVA")
        assertTrue(angry.firmness > .5f)
        assertTrue(angry.energyScale <= 1.06f)
        assertTrue(angry.pitchRangeScale <= 1.08f)
        assertTrue(angry.f0RegisterScale <= 1.02f)
    }
}
