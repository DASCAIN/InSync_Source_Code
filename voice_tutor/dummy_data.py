import datetime

DEFAULT_MEM0_USER_ID = '5033a22d-170c-4ac8-93ec-bf39035cdadb'


# Dummy Users
dummy_users = [
    {
        'id': 'prof_001',
        'email': 'prof.sharma@university.edu',
        'name': 'Dr. Anita Sharma',
        'role': 'professor',
        'avatar': None,
        'createdAt': datetime.date(2024, 1, 15),
    },
    {
        'id': 'student_001',
        'email': 'rahul.kumar@student.edu',
        'name': 'Rahul Kumar',
        'role': 'student',
        'avatar': None,
        'createdAt': datetime.date(2024, 2, 1),
    },
    {
        'id': 'student_002',
        'email': 'priya.singh@student.edu',
        'name': 'Priya Singh',
        'role': 'student',
        'avatar': None,
        'createdAt': datetime.date(2024, 2, 1),
    },
    {
        'id': 'student_003',
        'email': 'arjun.patel@student.edu',
        'name': 'Arjun Patel',
        'role': 'student',
        'avatar': None,
        'createdAt': datetime.date(2024, 2, 5),
    },
]

# Dummy Questions
dummy_questions = [
    {
        'id': 'Q001',
        'assignmentId': 'A001',
        'content': 'A jar contains 5 red marbles, 3 blue marbles, and 2 green marbles. If one marble is drawn at random, what is the probability of selecting a red marble?',
        'stepTypes': ['experiment', 'sample_space', 'event', 'counting', 'formula'],
        'order': 1,
    },
    {
        'id': 'Q002',
        'assignmentId': 'A001',
        'content': 'Two fair dice are rolled. What is the probability of getting a sum of eight?',
        'stepTypes': ['experiment', 'sample_space', 'event', 'counting', 'formula'],
        'order': 2,
    },
    {
        'id': 'Q003',
        'assignmentId': 'A001',
        'content': 'In a survey of 100 students, 60 prefer online classes and 40 prefer offline classes. If a student is selected at random, what is the probability that they prefer online classes?',
        'stepTypes': ['experiment', 'sample_space', 'event', 'formula'],
        'order': 3,
    },
    {
        'id': 'Q004',
        'assignmentId': 'A001',
        'content': 'The probability of a student passing an exam is 0.75. What is the probability that the student fails?',
        'stepTypes': ['event', 'formula', 'answer'],
        'order': 4,
    },
]

# Dummy Assignments
dummy_assignments = [
    {
        'id': 'A001',
        'title': 'Probability Basics',
        'topic': 'Probability',
        'description': 'Introduction to fundamental probability concepts including sample space, events, and basic probability calculations.',
        'difficulty': 'beginner',
        'questions': dummy_questions,
        'expectedDuration': 30,
        'cohortIds': ['C101'],
        'createdAt': datetime.date(2024, 2, 10),
    },
    {
        'id': 'A002',
        'title': 'Conditional Probability',
        'topic': 'Probability',
        'description': 'Learn about conditional probability, Bayes theorem, and their applications.',
        'difficulty': 'intermediate',
        'questions': [],
        'expectedDuration': 45,
        'cohortIds': ['C101'],
        'createdAt': datetime.date(2024, 2, 15),
    },
]

# Dummy Cohorts
dummy_cohorts = [
    {
        'id': 'C101',
        'name': 'JEE Probability Batch – 2026',
        'description': 'Intensive probability module for JEE aspirants',
        'professorId': 'prof_001',
        'studentIds': ['student_001', 'student_002', 'student_003'],
        'assignmentIds': ['A001', 'A002'],
        'createdAt': datetime.date(2024, 2, 1),
    },
]

# Dummy Student Progress
dummy_student_progress = [
    {
        'studentId': 'student_001',
        'assignmentId': 'A001',
        'questionsCompleted': 3,
        'totalQuestions': 4,
        'timeSpent': 1200,
        'hintsUsed': 2,
        'engagementScore': 85,
        'status': 'in_progress',
    },
    {
        'studentId': 'student_002',
        'assignmentId': 'A001',
        'questionsCompleted': 4,
        'totalQuestions': 4,
        'timeSpent': 1500,
        'hintsUsed': 1,
        'engagementScore': 92,
        'status': 'completed',
    },
    {
        'studentId': 'student_003',
        'assignmentId': 'A001',
        'questionsCompleted': 1,
        'totalQuestions': 4,
        'timeSpent': 300,
        'hintsUsed': 4,
        'engagementScore': 45,
        'status': 'in_progress',
    },
]

# Dummy Conversation Logs
dummy_conversation_logs = [
    {
        'id': 'conv_001',
        'studentId': 'student_001',
        'cohortId': 'C101',
        'assignmentId': 'A001',
        'questionId': 'Q001',
        'stepType': 'sample_space',
        'studentIntent': 'answering',
        'transcript': 'The total number of marbles is 10, right?',
        'timeSpent': 45,
        'hintCount': 0,
        'correctionsCount': 0,
        'createdAt': datetime.datetime(2024, 2, 12, 10, 30, 0),
    },
    {
        'id': 'conv_002',
        'studentId': 'student_001',
        'cohortId': 'C101',
        'assignmentId': 'A001',
        'questionId': 'Q001',
        'stepType': 'event',
        'studentIntent': 'asking_doubt',
        'transcript': 'How do I count favorable outcomes?',
        'timeSpent': 60,
        'hintCount': 1,
        'correctionsCount': 0,
        'createdAt': datetime.datetime(2024, 2, 12, 10, 31, 0),
    },
]

# Dummy Assignment Analytics
dummy_assignment_analytics = {
    'assignmentId': 'A001',
    'completionRate': 67,
    'averageTime': 25,
    'engagementScore': 74,
    'questionDifficulty': [
        {'questionId': 'Q001', 'score': 3.2},
        {'questionId': 'Q002', 'score': 4.5},
        {'questionId': 'Q003', 'score': 2.8},
        {'questionId': 'Q004', 'score': 1.5},
    ],
    'dropOffPoints': [
        {'questionId': 'Q002', 'count': 5},
        {'questionId': 'Q003', 'count': 2},
    ],
}

# Dummy Cohort Analytics
dummy_cohort_analytics = {
    'cohortId': 'C101',
    'overallEngagement': 74,
    'commonWeakConcepts': ['Sample Space Identification', 'Counting Favorable Outcomes'],
    'performanceDistribution': {
        'high': 1,
        'medium': 1,
        'low': 1,
    },
    'outliers': ['student_003'],
}

# Subjects Data
subjects = [
    {
        'id': 'SUB001',
        'name': 'Machine Learning',
        'description': 'Explore the fundamentals and advanced concepts of Machine Learning, from ensemble methods to deep learning architectures.',
        'topics': [
            {
                'id': 'TOP001',
                'subjectId': 'SUB001',
                'name': 'Bagging and Boosting',
                'description': 'Understand ensemble learning methods — Bagging reduces variance by training models on random subsets, while Boosting reduces bias by sequentially correcting errors. Together, they form the backbone of powerful algorithms like Random Forest and XGBoost.',
                'learningObjectives': [
                    'Understand the concept of ensemble learning and why it improves model performance',
                    'Differentiate between Bagging and Boosting techniques',
                    'Explain how Random Forest uses bagging to reduce variance',
                    'Describe how AdaBoost and Gradient Boosting sequentially reduce errors',
                    'Compare bias-variance trade-offs in bagging vs boosting',
                    'Apply ensemble methods to classification and regression problems',
                ],
                'videos': [
                    {
                        'id': 'VID001',
                        'title': 'Bagging',
                        'src': '/videos/boosting.mp4',
                        'description': 'Learn how Bootstrap Aggregating (Bagging) creates diverse models by training on random data subsets and combining their predictions.',
                    },
                    {
                        'id': 'VID002',
                        'title': 'Boosting',
                        'src': '/videos/bagging.mp4',
                        'description': 'Discover how Boosting builds strong learners by sequentially focusing on the mistakes of previous models.',
                    },
                ],
                'guidedAssignmentIds': ['A001'],
                'assignmentQuestions': [
                    {
                        'id': 'BQ001',
                        'content': 'A dataset contains 1,000 training samples. In bagging, how are multiple training datasets created from this original dataset?',
                        'type': 'mcq',
                        'options': [
                            'By removing noisy samples',
                            'By random sampling with replacement',
                            'By splitting dataset into equal parts',
                            'By normalizing feature values',
                        ],
                        'correctAnswer': 'b',
                        'conceptTags': ['Bootstrap sampling', 'Bagging basics'],
                        'difficulty': 'beginner',
                    },
                    {
                        'id': 'BQ002',
                        'content': 'If we create a bootstrap sample of size 1,000 from a dataset of 1,000 samples, what is most likely true?',
                        'type': 'mcq',
                        'options': [
                            'All original samples will appear exactly once',
                            'Some samples will appear multiple times',
                            'Dataset size becomes smaller',
                            'All samples will be unique',
                        ],
                        'correctAnswer': 'b',
                        'conceptTags': ['Sampling with replacement'],
                        'difficulty': 'beginner',
                    },
                    {
                        'id': 'BQ003',
                        'content': 'Explain why bagging helps reduce model variance.',
                        'type': 'descriptive',
                        'correctAnswer': 'Bagging reduces variance by training multiple models on different bootstrap samples and averaging their predictions. Since each model sees a slightly different subset of data, their individual errors tend to cancel out when combined, leading to a more stable and less variable prediction than any single model.',
                        'conceptTags': ['Variance reduction', 'Ensemble averaging'],
                        'difficulty': 'intermediate',
                    },
                    {
                        'id': 'BQ004',
                        'content': 'Five decision trees in a bagging ensemble produce the following predictions: 12, 14, 10, 13, 11. What is the final bagging prediction?',
                        'type': 'numerical',
                        'correctAnswer': '12',
                        'conceptTags': ['Averaging predictions', 'Regression bagging'],
                        'difficulty': 'beginner',
                    },
                    {
                        'id': 'BQ005',
                        'content': 'Five classifiers in a bagging ensemble predict the following classes: A, B, A, A, B. What is the final predicted class?',
                        'type': 'mcq',
                        'options': [
                            'Class A',
                            'Class B',
                            'Cannot be determined',
                            'Both A and B equally',
                        ],
                        'correctAnswer': 'a',
                        'conceptTags': ['Majority voting', 'Classification bagging'],
                        'difficulty': 'beginner',
                    },
                    {
                        'id': 'BQ006',
                        'content': 'Explain what Out-of-Bag (OOB) samples are and how they are used in bagging.',
                        'type': 'descriptive',
                        'correctAnswer': 'Out-of-Bag samples are the data points that were not selected in a particular bootstrap sample (approximately 37% of the original data). They serve as a natural validation set — each model can be evaluated on its OOB samples, providing an unbiased estimate of generalization error without needing a separate validation set.',
                        'conceptTags': ['Out-of-Bag estimation', 'Model validation'],
                        'difficulty': 'intermediate',
                    },
                    {
                        'id': 'BQ007',
                        'content': 'Why is Random Forest considered an extension of bagging? Explain the additional mechanism it introduces.',
                        'type': 'descriptive',
                        'correctAnswer': 'Random Forest extends bagging by not only creating bootstrap samples of data but also randomly selecting a subset of features at each split in the decision tree. This additional randomness (feature bagging) reduces correlation between trees, making the ensemble even more effective at reducing variance compared to standard bagging.',
                        'conceptTags': ['Multiple trees', 'Bootstrap sampling', 'Feature randomness'],
                        'difficulty': 'intermediate',
                    },
                    {
                        'id': 'BQ008',
                        'content': 'Bagging mainly helps reduce:',
                        'type': 'mcq',
                        'options': [
                            'Bias',
                            'Variance',
                            'Both bias and variance equally',
                            'It increases bias',
                        ],
                        'correctAnswer': 'b',
                        'conceptTags': ['Bias-variance tradeoff'],
                        'difficulty': 'beginner',
                    },
                    {
                        'id': 'BQ009',
                        'content': 'If individual decision trees in a bagging ensemble are overfitting the training data, explain how bagging still manages to improve generalization performance.',
                        'type': 'descriptive',
                        'correctAnswer': 'Even though individual trees overfit (high variance, low bias), bagging averages their predictions. Since each tree overfits to a different bootstrap sample, their errors are largely uncorrelated. Averaging uncorrelated high-variance models significantly reduces overall variance while preserving the low bias, resulting in better generalization.',
                        'conceptTags': ['Overfitting mitigation', 'Ensemble generalization'],
                        'difficulty': 'advanced',
                    },
                    {
                        'id': 'BQ010',
                        'content': 'If 100 bagged models are highly correlated with each other, what happens to the ensemble performance? Explain why model diversity matters.',
                        'type': 'descriptive',
                        'correctAnswer': 'If bagged models are highly correlated, the variance reduction benefit diminishes significantly. The variance of the ensemble average is proportional to the average correlation between models. Highly correlated models make similar errors, so averaging them provides little benefit. This is why model diversity is critical — techniques like Random Forest add feature randomness specifically to reduce inter-model correlation.',
                        'conceptTags': ['Model diversity importance', 'Correlation effect'],
                        'difficulty': 'advanced',
                    },
                ]
            }
        ]
    }
]

# Helper functions
def get_user_by_id(user_id):
    return next((u for u in dummy_users if u['id'] == user_id), None)

def get_students_by_ids(student_ids):
    return [u for u in dummy_users if u['id'] in student_ids and u['role'] == 'student']

def get_assignment_by_id(assignment_id):
    return next((a for a in dummy_assignments if a['id'] == assignment_id), None)

def get_cohort_by_id(cohort_id):
    return next((c for c in dummy_cohorts if c['id'] == cohort_id), None)

def get_student_progress(student_id, assignment_id):
    return next((p for p in dummy_student_progress if p['studentId'] == student_id and p['assignmentId'] == assignment_id), None)

def get_subject_by_id(subject_id):
    return next((s for s in subjects if s['id'] == subject_id), None)

def get_topic_by_id(subject_id, topic_id):
    sub = get_subject_by_id(subject_id)
    if not sub:
        return None
    return next((t for t in sub['topics'] if t['id'] == topic_id), None)
