CN miniproject

Server questions are loaded from `questions.json`.

To add more questions, append items in this format:

[
	{
		"question": "Your question text",
		"options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],
		"answer": "A"
	}
]

Notes:
- `answer` must match the option label (`A`, `B`, `C`, `D`, etc.).
- Keep at least 2 options for each question.
