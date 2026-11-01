# Discord direct reply lease

Physical Discord staging on 2026-11-01 showed a successful inbound returning `action=silence` with no outbound after the transport/schema fixes landed.

The group attention policy now distinguishes two response classes:

- explicit native reply, native mention, or explicit `Amora` vocative: bounded direct-response lease of 90 seconds;
- inferred same-speaker continuity: existing short 10-second reply lease inside the 35-second conversational floor.

A direct response remains invalidated when the floor generation changes, including another speaker taking the floor or transport edit/delete invalidation. The longer direct lease only prevents normal provider latency from turning a clearly addressed message into `REPLY_LEASE_INVALIDATED`.
