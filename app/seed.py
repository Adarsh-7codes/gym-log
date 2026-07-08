"""Seed the Exercise Library.

Idempotent: only inserts exercises whose name isn't already present, so it's
safe to run on every startup. ~8 exercises per body_part, spread across all
three difficulty levels so a beginner can start easy and progress.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BodyPart, Difficulty, Exercise

B, D = BodyPart, Difficulty

# (name, difficulty, equipment, short_instructions)
LIBRARY: dict = {
    B.chest: [
        ("Push-up", D.beginner, "Bodyweight", "Hands under shoulders, body straight, lower chest to floor and press up."),
        ("Machine Chest Press", D.beginner, "Machine", "Sit tall, press the handles forward until arms extend, control the return."),
        ("Incline Dumbbell Press", D.intermediate, "Dumbbells", "On a 30° bench, press dumbbells up and slightly together over the chest."),
        ("Barbell Bench Press", D.intermediate, "Barbell", "Lower the bar to mid-chest, elbows ~45°, press back up to lockout."),
        ("Dumbbell Fly", D.intermediate, "Dumbbells", "Slight elbow bend, open arms wide in an arc, squeeze chest to bring them back."),
        ("Decline Bench Press", D.advanced, "Barbell", "On a decline bench, lower the bar to lower chest and press up."),
        ("Cable Fly", D.advanced, "Cable machine", "From high pulleys, sweep hands down and together in a hugging arc."),
        ("Chest Dip", D.advanced, "Dip bars", "Lean forward, lower until shoulders below elbows, press back up."),
    ],
    B.back: [
        ("Lat Pulldown", D.beginner, "Cable machine", "Pull the bar to your upper chest, drive elbows down, control the return."),
        ("Seated Cable Row", D.beginner, "Cable machine", "Sit tall, pull the handle to your stomach, squeeze shoulder blades."),
        ("Assisted Pull-up", D.beginner, "Assisted machine", "Use the pad for help; pull chin over the bar, lower under control."),
        ("Single-arm Dumbbell Row", D.intermediate, "Dumbbell, bench", "Hand and knee on bench, row the dumbbell to your hip."),
        ("Bent-over Barbell Row", D.intermediate, "Barbell", "Hinge at hips, flat back, row the bar to your waist."),
        ("T-Bar Row", D.intermediate, "T-bar/landmine", "Hinge over the bar, pull the handles to your chest, squeeze the back."),
        ("Pull-up", D.advanced, "Pull-up bar", "Dead hang, pull chin over the bar, lower fully."),
        ("Deadlift", D.advanced, "Barbell", "Flat back, drive through the floor, stand tall, then lower under control."),
    ],
    B.legs: [
        ("Bodyweight Squat", D.beginner, "Bodyweight", "Feet shoulder-width, sit back and down to parallel, drive up."),
        ("Leg Press", D.beginner, "Machine", "Push the platform away to near-lockout, lower until knees ~90°."),
        ("Leg Extension", D.beginner, "Machine", "Extend knees to straighten legs, squeeze quads, lower slowly."),
        ("Goblet Squat", D.intermediate, "Dumbbell/kettlebell", "Hold the weight at your chest, squat down between your knees."),
        ("Romanian Deadlift", D.intermediate, "Barbell", "Soft knees, hinge at hips, lower the bar along the legs, feel the hamstrings."),
        ("Walking Lunge", D.intermediate, "Dumbbells", "Step forward, drop the back knee, push through the front heel."),
        ("Barbell Back Squat", D.advanced, "Barbell, rack", "Bar on upper back, squat to depth keeping the chest up, drive up."),
        ("Bulgarian Split Squat", D.advanced, "Dumbbells, bench", "Rear foot on bench, lower the front thigh to parallel, press up."),
    ],
    B.shoulders: [
        ("Machine Shoulder Press", D.beginner, "Machine", "Press the handles overhead to near-lockout, lower with control."),
        ("Dumbbell Lateral Raise", D.beginner, "Dumbbells", "Slight elbow bend, raise arms out to shoulder height, lower slowly."),
        ("Seated Dumbbell Press", D.beginner, "Dumbbells", "Press dumbbells from shoulder height overhead, control the descent."),
        ("Arnold Press", D.intermediate, "Dumbbells", "Start palms-in at chin, rotate and press overhead."),
        ("Front Raise", D.intermediate, "Dumbbells", "Raise the weight straight in front to shoulder height, lower slowly."),
        ("Reverse Pec Deck", D.intermediate, "Machine", "Open arms back in an arc to work the rear delts, squeeze, return."),
        ("Barbell Overhead Press", D.advanced, "Barbell", "From the front rack, press the bar overhead, brace the core."),
        ("Upright Row", D.advanced, "Barbell/cable", "Pull the bar up along the body to chest height, elbows leading."),
    ],
    B.arms: [
        ("Dumbbell Bicep Curl", D.beginner, "Dumbbells", "Elbows pinned, curl the weight up, squeeze, lower slowly."),
        ("Tricep Pushdown", D.beginner, "Cable machine", "Elbows at sides, push the bar down to lockout, control the return."),
        ("Hammer Curl", D.beginner, "Dumbbells", "Neutral grip, curl up keeping palms facing in."),
        ("Barbell Curl", D.intermediate, "Barbell", "Elbows fixed, curl the bar up, lower under control."),
        ("Skullcrusher", D.intermediate, "EZ bar, bench", "Lying down, bend elbows to lower the bar to your forehead, extend."),
        ("Concentration Curl", D.intermediate, "Dumbbell", "Elbow braced on inner thigh, curl the weight with a peak squeeze."),
        ("Close-grip Bench Press", D.advanced, "Barbell", "Hands shoulder-width, lower to chest, press up driving the triceps."),
        ("Preacher Curl", D.advanced, "EZ bar, preacher bench", "Arms on the pad, curl up without swinging, lower fully."),
    ],
    B.core: [
        ("Plank", D.beginner, "Bodyweight", "Forearms down, body straight, brace and hold without sagging."),
        ("Crunch", D.beginner, "Bodyweight", "Curl the shoulders off the floor, squeeze the abs, lower slowly."),
        ("Dead Bug", D.beginner, "Bodyweight", "On your back, extend opposite arm and leg, keep the low back flat."),
        ("Russian Twist", D.intermediate, "Weight plate", "Lean back, feet up, rotate the weight side to side."),
        ("Hanging Knee Raise", D.intermediate, "Pull-up bar", "Hang and pull the knees up to hip height, lower with control."),
        ("Cable Crunch", D.intermediate, "Cable machine", "Kneel, rope behind head, crunch down flexing the abs."),
        ("Hanging Leg Raise", D.advanced, "Pull-up bar", "Hang and raise straight legs to horizontal, no swinging."),
        ("Ab Wheel Rollout", D.advanced, "Ab wheel", "From the knees, roll out keeping a braced core, pull back in."),
    ],
}


def seed_exercises(db: Session) -> int:
    """Insert any library exercises that don't already exist. Returns count added."""
    existing = {name for (name,) in db.execute(select(Exercise.name)).all()}
    added = 0
    for body_part, rows in LIBRARY.items():
        for name, difficulty, equipment, instructions in rows:
            if name in existing:
                continue
            db.add(
                Exercise(
                    name=name,
                    body_part=body_part,
                    difficulty=difficulty,
                    equipment=equipment,
                    instructions=instructions,
                )
            )
            added += 1
    if added:
        db.commit()
    return added
