# **Advanced Architectural Frameworks for High-Precision Cybersecurity Event Synthesis and Incremental News Clustering**

The rapid evolution of the cyber threat landscape has transformed the nature of cybersecurity reporting from a periodic dissemination of advisories into a continuous, high-velocity stream of telemetry and analysis. For platforms like the kiber cybersecurity news aggregator, the primary challenge resides in the intelligent grouping of disparate RSS feed data into coherent, event-centric clusters without succumbing to the noise inherent in automated threat intelligence reports. Traditional clustering methods often rely on simplistic heuristics that fail to distinguish between shared stylistic registers and shared real-world incidents. This report provides an exhaustive analysis of the architectural, mathematical, and philosophical frameworks required to build a "smart" clustering engine that minimizes the "CVE snowball" effect and provides high-precision event synthesis even in low-attribute environments.

## **Theoretical Foundations of Topic Detection and Tracking in Cybersecurity**

The scientific discipline of Topic Detection and Tracking (TDT) provides the foundational paradigm for news aggregation. In the context of cybersecurity, the objective is to partition a corpus of reports into specific "stories"—sets of articles that describe the same underlying real-world event.1 Unlike generic news clustering, which might group articles by broad themes such as "sports" or "politics," cybersecurity clustering requires a higher degree of specificity, often referred to as event-centric clustering.1

An event in the cybersecurity domain is typically defined by a unique combination of actors, victims, vulnerabilities, and timeframes.3 The complexity of TDT for security news arises from the "braided nature" of these topics, where stories frequently overlap, merge, and split as new information emerges regarding attribution or patch availability.5 For instance, an initial report of a data breach may eventually be linked to a specific CVE, which is then attributed to a known threat actor, creating a multi-day narrative that spans multiple reporting styles and sources.

The shift from offline, batch-processed clustering to online, incremental clustering is a prerequisite for real-time news aggregation.1 In an online environment, articles must be processed "on the fly" as they arrive via RSS, without the luxury of knowing the total number of clusters in advance.1 This necessitates algorithms that can detect novel events while maintaining the integrity of existing clusters, a task complicated by the high dimensionality and sparsity of text data.7

## **Analysis of the Heuristic Failure: The kiber Case Study**

The current challenges observed in the kiber project—characterized by massive false-merge clusters and a self-reinforcing "CVE snowball"—stem from a reliance on a three-tier decision tree that prioritizes vocabulary similarity over semantic and entity-based identity.9 The existing framework utilizes CVE overlap, entity overlap (at a very low extraction rate), and More\_Like\_This (MLT) similarity.9

### **The Stylistic Gravity Well**

The most significant bottleneck in current implementations is the "gravity well" created by the stylistic register of cybersecurity reporting. Threat intelligence reports from vendors like Recorded Future, Mandiant, or CISA utilize a highly standardized vocabulary consisting of TTP terminology, campaign names, and IOC descriptions.10 When entity extraction fails—currently occurring in 94% of kiber’s ingested articles—the system defaults to the MLT tier, which calculates similarity based on a "bag-of-words" model.9

Because different stories (e.g., a report on a Russian espionage campaign and a report on a North Korean botnet) use the same technical "register," the MLT algorithm assigns high similarity scores to unrelated events.9 This stylistic matching fails to distinguish between the "how" (the language of security) and the "what" (the specific event).2 The result is a massive cluster of unrelated "threat intel style" content that lacks a single narrative core.

### **The CVE Snowball Mechanism**

The "CVE snowball" is a phenomenon where the accumulation of metadata within a cluster leads to an inevitable decline in precision. In the current kiber logic, when an article merges into a cluster, its CVEs are appended to the cluster’s global CVE pool.9 This creates a self-reinforcing loop:

1. A summary article or "roundup" (e.g., "Monthly CVE Landscape") is mistakenly merged into a cluster via stylistic MLT similarity.  
2. The roundup’s multiple CVE IDs are added to the cluster's index.  
3. Future, unrelated articles that mention any one of those CVEs are pulled into the cluster via the Tier 1 CVE check, even if they describe a completely different incident.9

To solve these issues, the system must transition to an architecture that prioritizes "signal entities" and employs a multi-dimensional distance function that accounts for the decay of information over time.

| Gap Identified | Root Cause | Proposed Structural Fix |
| :---- | :---- | :---- |
| **Low Entity Coverage (6%)** | Reliance on narrow regex/keyword extraction. | Hybrid Extraction (Gazetteers \+ Small-scale NER).12 |
| **MLT False Merges** | Bag-of-words matching on stylistic register. | Semantic Embeddings (Sentence Transformers) \+ Semantic Gating.13 |
| **CVE Snowball** | Unbounded growth of cluster metadata. | Medoid-based anchoring and Membership Capping.15 |
| **Identity Confusion** | Lack of "same-event" validation logic. | Philosophical shift to Story Identification.2 |

## **Solving the Entity Extraction Crisis: Moving Beyond 6%**

The absence of structured signal in 94% of ingested articles is the primary driver of clustering failure. Without entities (CVEs, Actors, Malware), the system lacks the high-precision anchors needed to differentiate events. Improving this coverage requires a move from basic regex to a more robust, multi-layered extraction strategy that maximizes "attribute density" for every article.3

### **Strategic Use of Gazetteers and Unified Taxonomies**

A major hindrance in cybersecurity NER is the lack of standardized naming conventions. Threat actors are frequently referred to by multiple aliases (e.g., APT28 is also known as Fancy Bear, Strontium, or Forest Blizzard).17 A "smart" aggregator must implement a normalization layer that maps these aliases to a single entity key.19

The adoption of the STIX 2.1 taxonomy allows for a consistent classification of entities.3 By integrating public datasets and curated lists—such as the MITRE ATT\&CK Group list, Malpedia, and Google Threat Intelligence—the system can build exhaustive gazetteers for several critical entity types.20

| Signal Type | Example Entities | Extraction Strategy |
| :---- | :---- | :---- |
| **Threat Actor** | Cozy Bear, Lazarus, APT41 | Normalization via Alias Tables (e.g., Microsoft/CrowdStrike naming).17 |
| **Malware Family** | Emotet, TrickBot, Cobalt Strike | Lookup against Malpedia-aligned gazetteers.20 |
| **Vulnerability** | CVE-2024-1234, MS17-010 | Regex \+ Validation against NVD/KEV catalogs.12 |
| **Tool** | Mimikatz, BloodHound, AdFind | Integration with MITRE ATT\&CK Tooling database.21 |
| **Target Sector** | Financial, Healthcare, Telecomm | Rule-based mapping of industry keywords.10 |

### **Heuristics for Low-Attribute Extraction**

When articles lack specific IDs like CVEs, the system must extract "thematic subject terms" to increase attribute density.24 This involves identifying rare, domain-specific keywords that carry high information value. Traditional TF-IDF weighting can be modified to prioritize "content words" (e.g., "zero-day," "exfiltration," "ransomware") over "procedural language" (e.g., "advisory," "report," "update").25

A crucial component of this heuristic is the removal of "cybersecurity stop words." While general NLP libraries like NLTK or spaCy provide generic stop word lists, cybersecurity news requires a custom list to eliminate the noise created by the standardized "intel register".27

**Proposed Cybersecurity Stop Word Categories:**

* **Report Identifiers:** advisory, bulletin, alert, roundup, brief, summary.  
* **Generic Security Verbs:** patched, mitigated, discovered, reported, updated.  
* **Source Boilerplate:** recorded, future, mandiant, cisa, blog, post.

By filtering these terms, the remaining tokens—even if sparse—become highly distinguishing features for similarity calculations.25

## **Similarity Architectures: The Multidimensional Distance Function**

To minimize the need for heavy AI while maintaining "smart" clustering, the system should adopt a weighted ensemble of distance metrics. This approach moves beyond the binary "merge/no-merge" decision and allows for a more nuanced assessment of story identity.14

### **Mathematical Formulation of Similarity**

The distance ![][image1] between an incoming article ![][image2] and an existing cluster ![][image3] should be a composite score:

![][image4]  
where ![][image5] represents the weight assigned to each signal.

#### **1\. Semantic Distance (![][image6])**

Instead of the vocabulary-based more\_like\_this (MLT), the system should utilize dense semantic embeddings.13 Small-scale transformers (e.g., BERT-based models) can convert article summaries into vectors in a high-dimensional space.4 The distance is then calculated using Cosine Similarity:

![][image7]  
Unlike MLT, embeddings capture the "intent" and "meaning" of the text, allowing the system to recognize that two articles are about the same event even if they use different phrasing.29 For computational efficiency, the system should only embed the title and a one-sentence summary, as full-text embeddings often introduce noise from boilerplate language.14

#### **2\. Entity Distance (![][image8])**

Entity similarity is measured using the Jaccard index, which compares the overlap of signal entity sets 14:

![][image9]  
where ![][image10] represents the set of signal entities (CVE, Actor, Malware, Tool). A "smart" logic gives zero weight to "Vendor" entities (e.g., Microsoft, Google) unless they are paired with a specific product or version, as they are too broad to serve as signal.9

#### **3\. Temporal Distance (![][image11])**

Cybersecurity news is highly volatile; events occur in rapid bursts and their relevance decays quickly.1 The temporal distance ![][image11] acts as a "gravity" factor that pulls articles apart as the time gap increases.14

![][image12]  
A fading factor ![][image13] is often applied to ensure that past articles lose influence over current clustering decisions.2

## **Incremental Clustering for Unbounded News Streams**

The continuous nature of RSS feeds necessitates a clustering algorithm that operates without a predefined number of clusters (![][image14]) and can adapt to "concept drift".1

### **The K-Medoids Approach for Cluster Stability**

The "CVE snowball" and "centroid drift" seen in the kiber project are common failings of K-Means-style algorithms. In K-Means, the center of a cluster (the centroid) is a mathematical average of all its members. As the cluster grows, the centroid can drift away from the original event toward a broader, more generic topic.15

A superior alternative for security news is the **K-Medoids** (or Partitioning Around Medoids \- PAM) algorithm.15 A medoid is an actual data point—the most representative article in the cluster—that has the minimum average dissimilarity to all other points.15

**Advantages of K-Medoids for News Aggregation:**

* **Robustness to Outliers:** Roundup articles with 50 CVEs will not shift the medoid as drastically as they would shift a mean-based centroid.15  
* **Interpretability:** The "center" of a cluster is a real news article that can be displayed to the user as the "source" or "summary" of the event.33  
* **Stability:** Medoids change only when a significantly more representative article is ingested, preventing the gradual drift that leads to "Mega-Clusters".34

### **Micro-Clustering and the Shared Density Graph**

For high-velocity streams, the system can utilize a two-phase clustering approach like **DBSTREAM**.31

1. **Online Phase:** Articles are grouped into "micro-clusters" (very tight groups of near-identical reports).31  
2. **Offline Phase:** A "shared density graph" tracks the relationship between micro-clusters. If two micro-clusters consistently share signal (e.g., the same actor or CVE), they are merged into a "macro-cluster" representing the full story.6

This architecture allows the system to be "conservative" at ingest time (creating small, high-precision groups) and "intelligent" at query time (merging related groups into a single narrative).31

## **Solving the CVE Snowball: Membership Logic and Gating**

The "snowball" effect occurs because the current logic treats every article within a cluster as an equal contributor to the cluster's identity.9 To restore precision, the project must adopt a more hierarchical and restrictive membership philosophy.

### **Seed-Based Gating**

Instead of matching incoming articles against a cluster’s *accumulated* pool of CVEs, the system should only match against **Seed Entities**.9

* When a cluster is first created, the entities from the first two articles are designated as the "Seed Pool."  
* Subsequent articles can join the cluster if they match the Seed Pool.  
* While these new articles may bring in *new* CVEs (e.g., a follow-up report finding a second vulnerability), these are stored as "Secondary Metadata" and are not used as primary keys for further merging.9

### **The "Roundup Cap" and Cluster Hygiene**

The "roundup cap" (skipping Tier 1 if an article has ![][image15] CVEs) is a useful starting point, but it must be paired with cluster hygiene.9

* **Cluster Weighting:** Articles that join a cluster via stylistic MLT should have a lower "weight" than those that join via a hard CVE match.31  
* **Membership Limiting:** If a cluster exceeds a certain size (e.g., 50 articles), the similarity threshold for new members should be automatically increased to prevent it from becoming a "gravity well" for generic news.2  
* **Outlier Pruning:** Periodically, a "cleanup" process should identify articles within a cluster that have high dissimilarity to the medoid and move them to a different cluster or label them as noise.14

## **Philosophical Framework for Story Identification**

The ultimate goal of the aggregator is to distinguish between "Topic" (broad, recurring themes) and "Event" (specific, one-time incidents).2

### **Event-Centric Clustering Logic**

A "smart" system follows the philosophy that articles describe the same **Event** if they share a specific identifier AND a narrow timeframe.1

* **Identifier:** A CVE ID, a malware hash, or a unique campaign name (e.g., "Operation Woolen-Goldfish").12  
* **Temporal Constraint:** Security events usually have a reporting peak within 24–48 hours.5 A Tier 2 "Entity Overlap" check should have a strict window (e.g., 48 hours), while a Tier 1 "CVE Overlap" check can have a broader window (e.g., 7 days).2

### **Handling Low-Attribute Related Stories**

The user's query highlights a common scenario: one news source has a CVE, but another related source does not. How should they be clustered? \[User Query\].

1. **Deductive Entity Propagation:** If Article A (with CVE-1) and Article B (no CVE) are linked via high semantic similarity (Tier 4), Article B can "inherit" the CVE-1 tag for clustering purposes.4  
2. **Graph-Based Linking:** Instead of a flat decision tree, the system can use a "Local Topic Graph".40 If Article A (CVE-1) and Article B (No CVE) both mention "Fortinet FortiGate RCE," they are linked to the same entity nodes in a graph. A community detection algorithm can then identify them as part of the same event cluster regardless of the missing CVE tag in Article B.41

| Story Element | High-Confidence Match (Identity) | Low-Confidence Match (Context) | Actionable Strategy |
| :---- | :---- | :---- | :---- |
| **Vulnerability** | Identical CVE-ID | Same Vendor (e.g., Cisco) | Use CVE as anchor; gate Vendor with Semantic similarity.9 |
| **Threat Actor** | Same Alias (e.g., APT29) | Same Origin (e.g., Russia) | Normalize aliases; treat Origin as low-weight feature.17 |
| **Incidence** | Shared Malware Hash | Shared Attack Type (e.g., Phishing) | Use Hash as anchor; treat Attack Type as thematic label.44 |

## **Minimizing AI: Practical Heuristics for the kiber Aggregator**

While LLMs are powerful for NER and deduplication, they are computationally expensive. A "minimized AI" approach focuses on maximizing deterministic heuristics and using smaller, specialized models only where they add the most value.45

### **The Optimized Decision Tree**

To avoid the "MLT gravity well," the decision tree should be re-ordered and gated:

1. **Tier 1: Deterministic Identity (CVE/Hash)**  
   * Check for unique, high-precision IDs.  
   * *Gate:* If ![][image15] CVEs, move to Tier 2 but do not form a new cluster.  
   * *Time Window:* 7 days.9  
2. **Tier 2: Entity-Aware Mapping (Normalized Actors/Malware)**  
   * Use normalized gazetteers to match aliases.  
   * *Gate:* Must share 2+ signal entities (Actors, Malware, Tools). Vendors are excluded unless a specific version is mentioned.  
   * *Time Window:* 48 hours.9  
3. **Tier 3: Semantic Verification (The "Smart" Gate)**  
   * Generate article embeddings using a small transformer (e.g., all-MiniLM-L6-v2).  
   * Compare incoming article ![][image2] against the **Medoid** of the most likely cluster ![][image3].  
   * *Gate:* If Cosine Similarity ![][image16], reject the merge and create a new cluster, even if MLT vocabulary scores are high.14  
4. **Tier 4: Narrative Linking (Optional/Query-time)**  
   * If no merge is found, check if the article belongs to a broader "Campaign" or "Vendor Beat" using a lower semantic threshold and a longer time window (30 days). This is used for "related stories" in the UI rather than core event clustering.2

### **Strategic Use of "Noise" Labeling**

Many articles in cybersecurity feeds are not "events" but "maintenance" (e.g., routine patch notices).

* **Rule-based Outlier Detection:** Any article where ![][image17] of the content matches a known "boilerplate" template (e.g., standard CISA headers) should be flagged.46  
* **Density-based Noise Handling:** Algorithms like HDBSCAN or DBSTREAM can explicitly label articles that do not belong to any dense region as "noise" rather than forcing them into a cluster.6 This prevents the creation of "garbage clusters" that attract more unrelated news.

## **Conclusion and Implementation Roadmap**

The path forward for the kiber project requires a transition from a word-based heuristic system to a semantic-and-entity-centric framework. By addressing the 6% entity extraction gap through normalization and gazetteers, the project can shift the "heavy lifting" away from unreliable MLT similarity and toward high-precision identity markers.

**Key Technical Recommendations:**

* **Adopt K-Medoids:** Anchor clusters to representative articles to prevent centroid drift and the CVE snowball.15  
* **Implement a Semantic Gate:** Use dense embeddings to validate merges, effectively filtering out stylistic false positives.13  
* **Normalize Entities:** Move beyond simple regex to a mapping layer that recognizes threat actor and malware aliases.19  
* **Apply Temporal Fading:** Use a strictly defined 7-day window for event-centricity, pushing articles further apart as they age.2

By following this philosophy, a cybersecurity news aggregator can successfully synthesize the "news firehose" into a coherent set of events, providing analysts with a clear view of the threat landscape without the distraction of redundant or mis-clustered reports. The integration of deterministic identity anchors with semantic validation provides a robust, "smart" system that functions effectively even in the low-attribute environment of RSS-based data collection.

#### **Works cited**

1. Incremental Clustering of News Reports \- MDPI, accessed April 21, 2026, [https://www.mdpi.com/1999-4893/5/3/364](https://www.mdpi.com/1999-4893/5/3/364)  
2. Real-time News Story Identification \- arXiv, accessed April 21, 2026, [https://arxiv.org/html/2508.08272v1](https://arxiv.org/html/2508.08272v1)  
3. Recognizing and Extracting Cybersecurity Entities from Text \- UMBC ebiquity, accessed April 21, 2026, [https://ebiquity.umbc.edu/get/a/publication/1152.pdf](https://ebiquity.umbc.edu/get/a/publication/1152.pdf)  
4. Event-Driven News Stream Clustering using Entity-Aware Contextual Embeddings, accessed April 21, 2026, [https://cdn.amazon.science/99/5b/19d4cf374b64a130b50975856446/event-driven-news-stream-clustering-using-entity-aware-contextual-embeddings.pdf](https://cdn.amazon.science/99/5b/19d4cf374b64a130b50975856446/event-driven-news-stream-clustering-using-entity-aware-contextual-embeddings.pdf)  
5. incremental visual text analytics of news story development \- KOPS, accessed April 21, 2026, [https://kops.uni-konstanz.de/bitstreams/fe015717-4dac-4809-ad7c-9e51862713b5/download](https://kops.uni-konstanz.de/bitstreams/fe015717-4dac-4809-ad7c-9e51862713b5/download)  
6. Online Density-Based Clustering for Real-Time Narrative Evolution Monitoring \- arXiv, accessed April 21, 2026, [https://arxiv.org/html/2601.20680v2](https://arxiv.org/html/2601.20680v2)  
7. Data Stream Clustering Techniques, Applications, and Models: Comparative Analysis and Discussion \- MDPI, accessed April 21, 2026, [https://www.mdpi.com/2504-2289/2/4/32](https://www.mdpi.com/2504-2289/2/4/32)  
8. Chapter 4 A SURVEY OF TEXT CLUSTERING ALGORITHMS, accessed April 21, 2026, [https://jlu.myweb.cs.uwindsor.ca/8380/text-cluster.pdf](https://jlu.myweb.cs.uwindsor.ca/8380/text-cluster.pdf)  
9. accessed January 1, 1970, [https://github.com/OmarHackerPro/kiber](https://github.com/OmarHackerPro/kiber)  
10. The 13 Must-Follow Threat Intel Feeds | Wiz, accessed April 21, 2026, [https://www.wiz.io/academy/threat-intel/must-follow-threat-intel-feeds](https://www.wiz.io/academy/threat-intel/must-follow-threat-intel-feeds)  
11. Language Style Matching : A Comprehensive List of Articles and Tools \- OSF, accessed April 21, 2026, [https://osf.io/preprints/psyarxiv/yz4br](https://osf.io/preprints/psyarxiv/yz4br)  
12. CyberNER: A Harmonized STIX Corpus for Cybersecurity Named Entity Recognition \- arXiv, accessed April 21, 2026, [https://arxiv.org/html/2510.26499v1](https://arxiv.org/html/2510.26499v1)  
13. Topic Detection and Tracking with Time-Aware Document Embeddings \- arXiv, accessed April 21, 2026, [https://arxiv.org/html/2112.06166v2](https://arxiv.org/html/2112.06166v2)  
14. How I Built a News Aggregation Algorithm | by Zeyong Cai | Medium, accessed April 21, 2026, [https://medium.com/@zeyongcai/how-i-built-a-news-aggregation-algorithm-part-1-9e43066861d7](https://medium.com/@zeyongcai/how-i-built-a-news-aggregation-algorithm-part-1-9e43066861d7)  
15. Medoids – Knowledge and References \- Taylor & Francis, accessed April 21, 2026, [https://taylorandfrancis.com/knowledge/Engineering\_and\_technology/Computer\_science/Medoids/](https://taylorandfrancis.com/knowledge/Engineering_and_technology/Computer_science/Medoids/)  
16. Real time clustering and filtering with RTMAC and RTEFC \- Greg Stanley and Associates, accessed April 21, 2026, [https://gregstanleyandassociates.com/whitepapers/BDAC/Clustering/clustering.htm](https://gregstanleyandassociates.com/whitepapers/BDAC/Clustering/clustering.htm)  
17. How Microsoft names threat actors \- Unified security operations, accessed April 21, 2026, [https://learn.microsoft.com/en-us/unified-secops/microsoft-threat-actor-naming](https://learn.microsoft.com/en-us/unified-secops/microsoft-threat-actor-naming)  
18. Unveiling the Threat Actors. In this blog, we will discuss some of… | by Abhinav Pathak | CodeX | Medium, accessed April 21, 2026, [https://medium.com/codex/unveiled-the-threat-actors-eb18e3221251](https://medium.com/codex/unveiled-the-threat-actors-eb18e3221251)  
19. Kickstart Threat Actor Research with Threat Actor Cards | Recorded Future, accessed April 21, 2026, [https://www.recordedfuture.com/blog/threat-actor-cards](https://www.recordedfuture.com/blog/threat-actor-cards)  
20. malware\_name\_mapping/mapping.csv at master \- GitHub, accessed April 21, 2026, [https://github.com/certtools/malware\_name\_mapping/blob/master/mapping.csv](https://github.com/certtools/malware_name_mapping/blob/master/mapping.csv)  
21. MITRE ATT\&CK®, accessed April 21, 2026, [https://attack.mitre.org/](https://attack.mitre.org/)  
22. Groups | MITRE ATT\&CK®, accessed April 21, 2026, [https://attack.mitre.org/groups/](https://attack.mitre.org/groups/)  
23. APT\_REPORT/summary/2024/threat actor list from cs.csv at master \- GitHub, accessed April 21, 2026, [https://github.com/blackorbird/APT\_REPORT/blob/master/summary/2024/threat%20actor%20list%20from%20cs.csv](https://github.com/blackorbird/APT_REPORT/blob/master/summary/2024/threat%20actor%20list%20from%20cs.csv)  
24. Thematic clustering of text documents using an EM-based approach \- PMC, accessed April 21, 2026, [https://pmc.ncbi.nlm.nih.gov/articles/PMC3465205/](https://pmc.ncbi.nlm.nih.gov/articles/PMC3465205/)  
25. Stopwords in technical language processing \- PMC \- NIH, accessed April 21, 2026, [https://pmc.ncbi.nlm.nih.gov/articles/PMC8341615/](https://pmc.ncbi.nlm.nih.gov/articles/PMC8341615/)  
26. Class Explanations: the Role of Domain-Specific Content and Stop Words \- ACL Anthology, accessed April 21, 2026, [https://aclanthology.org/2023.nodalida-1.12.pdf](https://aclanthology.org/2023.nodalida-1.12.pdf)  
27. NLP Stop Words Guide | Text Processing Optimization \- Inventive HQ, accessed April 21, 2026, [https://inventivehq.com/blog/nlp-stop-words-guide-enhance-efficiency-inventivehq](https://inventivehq.com/blog/nlp-stop-words-guide-enhance-efficiency-inventivehq)  
28. To Use or Lose: Stop Words in NLP | by Moirangthem Gelson Singh | Medium, accessed April 21, 2026, [https://medium.com/@gelsonm/to-use-or-lose-stop-words-in-nlp-de946edaa468](https://medium.com/@gelsonm/to-use-or-lose-stop-words-in-nlp-de946edaa468)  
29. Clustering news articles \- newscatcher, accessed April 21, 2026, [https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/clustering-news-articles](https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/clustering-news-articles)  
30. Ultimate Guide To Text Similarity With Python | NewsCatcher, accessed April 21, 2026, [https://www.newscatcherapi.com/blog-posts/ultimate-guide-to-text-similarity-with-python](https://www.newscatcherapi.com/blog-posts/ultimate-guide-to-text-similarity-with-python)  
31. DBSTREAM \- River, accessed April 21, 2026, [https://riverml.xyz/dev/api/cluster/DBSTREAM/](https://riverml.xyz/dev/api/cluster/DBSTREAM/)  
32. Clustering Stream Data by Exploring the Evolution of Density Mountain \- VLDB Endowment, accessed April 21, 2026, [http://www.vldb.org/pvldb/vol11/p393-gong.pdf%3C/ee%3E](http://www.vldb.org/pvldb/vol11/p393-gong.pdf%3C/ee%3E)  
33. Exploring the World of Clustering: K-Means vs. K-Medoids | by Prasan N H | Medium, accessed April 21, 2026, [https://medium.com/@prasanNH/exploring-the-world-of-clustering-k-means-vs-k-medoids-f648ea738508](https://medium.com/@prasanNH/exploring-the-world-of-clustering-k-means-vs-k-medoids-f648ea738508)  
34. 2\. Clustering with KMedoids, CLARA and Common-nearest-neighbors, accessed April 21, 2026, [https://scikit-learn-extra.readthedocs.io/en/latest/modules/cluster.html](https://scikit-learn-extra.readthedocs.io/en/latest/modules/cluster.html)  
35. k-medoids \- Wikipedia, accessed April 21, 2026, [https://en.wikipedia.org/wiki/K-medoids](https://en.wikipedia.org/wiki/K-medoids)  
36. Centroid vs Medoid in Clustering \- Machine Learning \- Scribd, accessed April 21, 2026, [https://www.scribd.com/document/807684270/Unit-4](https://www.scribd.com/document/807684270/Unit-4)  
37. Online Density-Based Clustering for Real-Time Narrative Evolution Monitoring \- arXiv, accessed April 21, 2026, [https://arxiv.org/html/2601.20680v1](https://arxiv.org/html/2601.20680v1)  
38. What is Indicator of Compromise? \- Vectra AI, accessed April 21, 2026, [https://www.vectra.ai/topics/indicator-of-compromise](https://www.vectra.ai/topics/indicator-of-compromise)  
39. Why Keyword Coverage Fails Without Entity Coverage \- The HOTH, accessed April 21, 2026, [https://www.thehoth.com/blog/entity-based-seo/](https://www.thehoth.com/blog/entity-based-seo/)  
40. Dense vs. Sparse Representations for News Stream Clustering \- CEUR-WS.org, accessed April 21, 2026, [https://ceur-ws.org/Vol-2342/paper6.pdf](https://ceur-ws.org/Vol-2342/paper6.pdf)  
41. Illustration of two different event detection methods \- ResearchGate, accessed April 21, 2026, [https://www.researchgate.net/figure/Illustration-of-two-different-event-detection-methods\_fig1\_371682872](https://www.researchgate.net/figure/Illustration-of-two-different-event-detection-methods_fig1_371682872)  
42. Graph-based Event Extraction from Twitter \- ACL Anthology, accessed April 21, 2026, [https://aclanthology.org/R17-1031/](https://aclanthology.org/R17-1031/)  
43. Event Graph-Based News Clustering: The Role of Named Entity-Centered Subgraphs, accessed April 21, 2026, [https://www.semanticscholar.org/paper/Event-Graph-Based-News-Clustering%3A-The-Role-of-K%C3%B6me%C3%A7o%C4%9Flu-Y%C4%B1lmaz/1b99a58b6b73c593399e53ff671f7ca5f23226eb](https://www.semanticscholar.org/paper/Event-Graph-Based-News-Clustering%3A-The-Role-of-K%C3%B6me%C3%A7o%C4%9Flu-Y%C4%B1lmaz/1b99a58b6b73c593399e53ff671f7ca5f23226eb)  
44. Complete Guide to Understanding Indicators of Compromise (IoCs) \- Palo Alto Networks, accessed April 21, 2026, [https://www.paloaltonetworks.com/cyberpedia/indicators-of-compromise-iocs](https://www.paloaltonetworks.com/cyberpedia/indicators-of-compromise-iocs)  
45. Generative AI in NLP: Proposed Techniques For Cybersecurity Vulnerability Detection | TechRxiv, accessed April 21, 2026, [https://www.techrxiv.org/doi/10.36227/techrxiv.173895015.56244494](https://www.techrxiv.org/doi/10.36227/techrxiv.173895015.56244494)  
46. Turning threat reports into detection insights with AI | Microsoft Security Blog, accessed April 21, 2026, [https://www.microsoft.com/en-us/security/blog/2026/01/29/turning-threat-reports-detection-insights-ai/](https://www.microsoft.com/en-us/security/blog/2026/01/29/turning-threat-reports-detection-insights-ai/)  
47. Threat Actor Groups Tracked by Palo Alto Networks Unit 42 (Updated Aug. 1, 2025), accessed April 21, 2026, [https://unit42.paloaltonetworks.com/threat-actor-groups-tracked-by-palo-alto-networks-unit-42/](https://unit42.paloaltonetworks.com/threat-actor-groups-tracked-by-palo-alto-networks-unit-42/)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABEAAAAXCAYAAADtNKTnAAAA00lEQVR4Xu2SLQ7CQBCFHwkCEhw4OAcKyQ1AkGBwSBycA4XGg+AaOBLqEVyCEH5mmE6zedltNUm/5InuN93uzhSoqaIn+VRkX1RX8Ia90GQhHCUvyYRFSAu2wZNFgJ8qSR9WkLEI8JM2WDgbWMGSRYBv0mHh3GCn0Can8Ovo1aNoLw6IN1XRr5f2RF9UOWIRMIfVXFg4Y6RH6zwkZ0mXheNNTdGG+SELZyC5Iz3aNWyDBa3/0FlPJVdY0Sl/1swk23x9h5KJ6d2827HoL74qqmv+jC+HXTt0ZxBpFgAAAABJRU5ErkJggg==>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAYCAYAAAAlBadpAAAAwklEQVR4XmNgGNaAFYjF0AWJBc+A+D+6IDGAhQGikSzNmxnI1CwMxM1A/JwBopkHVRo/KAZiWSB+yADRLIkqjRtMAOIDUDaIBmk2hkniA6ComQ+lQWAhA0SzL1wFHhAExNpIfJhmkDhekMGACF10XIWkDiu4DcSTgDgECXcyQDSDXIATgALEFV2QASL+lQEScFijqxKI/6ILQoEMED9hgEQZSnQxAnEWA8JfcUAshCRvDsSroXIgwycDsRWS/CgY/AAA0for2ohd+yUAAAAASUVORK5CYII=>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAZCAYAAADuWXTMAAAA40lEQVR4Xu2Tvw4BQRCHR6IREuEFdBKJROdFlHQeQucJVEoNovASCp1S5U+jUVJIFAoKfr/M3WVvcHc6xX3Jl1xmdm42O7siKT45eIJPuIcjuIRjmIfnYKWhCtfwDvsmd4AT0Z++0RJNzGzCoyC6AxqiCS9wAYsm5zL0DMiIdqT8jqIHa26gLlrIznGwuOQGNqLFbTeYlBu8woZNJIFdeVCc4c+weGqDH+CF4fmEeEh8Z05hDrM2wRNk945NeFTgTr6MkZcialRbOLBBl7LotvzLchR9HCt3URxd0VdE+Z3yF7wASGUvBHgO0DYAAAAASUVORK5CYII=>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAAAsCAYAAADYUuRgAAAI2klEQVR4Xu3da8h92RzA8SXkOu6R0LglSi65xVCTWxQSo1EzL7xCImWSywvGC0UppYkSSXIJRSFeeDEijRSpEbnUM5pShAi55LK+rf3r+f3Xf+9z9j7nPOc5z5nvp1b/vdfe5zznrLX3Xr+z1tr7X4okSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZIkSZLWe1CfsWd3r+lOfaYkSdIxul9NDx1Jq9y1piv7zBEPr+m2PnOBl9T085p+U9Ozhryn1HSvYfmP5fiDtr5e5tSP9meT80fLWMaSVD24ptfW9LtyeiG8uqY/1/SfMh4Q/a3PmMB+c/fN7lzTv2p6Zcp7Rk2frOl/Ke8eNf0+rR8byoH6+HdZVj/anzh/OC6vKtbPWdjkGiVJR+ndNb2hz6xurelnXR69Xjd1eWNeVdrFNAdYc9y3tNdc22+obqnph13et2p6dpd3bCiPufVzTN7WZxwozp+x4/wi1M8n+owDteQaJUlH66SMz0l7YWkNUf4F+4eaHpLWp/yypveX8YZsFfb/Yp85eEdN13V5TyjHfcF+WGllsqp+jhX1fRGc1PTTPrOMnz+H5tN9xoE6KavPgUMuY0namalGPy6G9055U/tm/GrnNQRXc/YPny+rL778yr5/n1mW/Y1NMYfuo2md4crHDsuPq+mGtG2XCFqmvp8B26mon3zsPC0tf6W0uZdngToY6/0ZO38OzZKAjaHIj5c2FSE8orRzAZ9J+bs2dZxfhDKWpJ2IHpwxHyltG3dkggv2ujlpXDg/NiwvvZiy71hPxTq87oo+c8ANCtfMSPeJF4x4UU2PL61xi4DgyzV9Y1j+SZkuw22dlOkyifo5VnMDtlw/nx3y7lJOj90HDssvH7bt0qoe0P78OURzAzbmkb2utDK8PeXz/ejljuWzsOQaJUlHa1UPDsOf/03r9FjcltbHMBQanlzae8+5o4t92Hesp2Idgsg5f2NT8Z3+mfIol5em9b+n5TmeXtOr+8wRq8qkr59jMzdgy/XD5HT0vY8Mz3PjyhIEA+ssOX8O0dyAjfmj8S894SBIzd+dm2OWeExNr+8zR1z0MpakneBiR29Rj4n8XCTpnQjrAravlUt7ra4v7T1oPNeJgC0PY2V8nvgl3yNgm9q2KzkAYA5fbkD4dR9B1QtK+6zrGkIavVv7zE78TXqLemP1c15oNLNdfaa5ARv6AO3XpQVpIXpDY74jvb5TQQDWbQ9Lzp8l6FXNvUa5l7rfto11x2kWAVr0MjP1Id8EFGVM2V9d2mdcVYbUz5y7vJeUMUF2vhZsWv6SdFDigvrcLp8LMvl9DxAXwqkhUZ659q4+s7T3mXu3H/tONdI8i20KrxsbkgK9fLx2XXpevGACDca3h2WCsxykvLlcOq+HoHNJQziFBnCs12Kqfpij1fc0xqNBAsEmyIu5Ryz38wZp6GI74j14JhYpy2VBYNHPFeO1fd4cU8fCmFw/oHxyw83dxPhHOQ2Ap4LhuZaeP2BoMYIvypey5H3Iz3KvEWWXA7Z+W18fSyw5TpmT+pe0Tr3n3t8nDv/yWSkDjrXcK72JpWWcj8V8/OZyWnfsb3q8StKZGevBedKQ9/yUF6Z+MXPB4xEeY9g/NwpcUMfeAzeUtq2/WP62XH5Rzabeb5foTbh5WOYuVj5T+F5axq4CtrGehan6eVRpj1zhBoh4VMM3y+mNEXwetjMfKBp83ufaYTk3xPFAYgImeixYZu4Sz8EC7xUBHEFRDB3yGQj0opEmiCeIisZ1qSUBW64fjh/+3qOHdb5j39NCufyiy1tq7PyJ79rXD4+rib/HI2/wntJ6rAkeKN+YY8ePj+it4r35MZGPt35bBCkcd7lXcY4lxylTAPIPNr5n3LX9yJSPL5T2uVadt3OMlfHUOYAcsNHDH3ecf7i0sn7nsD517Me5E3MhJelc0XhxweoTQddb035jaOzzr/2by6XvEd7S5ce2D5bWa5TfI3tZafvy4Ny/1vSpSzdfhgCkH5I7CzQ8PKiTYIb/beFXw/Kf8k6DbQO2uImhT6vqh8aUfd43rBM88XnfVNr/FkEQc0VpDewrhn2i8SUQjxsb6C0kIYaXCCgIHClrsG/UXx6C4gYPelluGtY5VrZpsJcEbFE/BJX0mDJfjfJgPc81BPtG8LmJqfOH9IO0X3Z7TW8s7bh475BHufIaUI4cN6DXikAF1Bu9tzGUi7yNujsZ1jlXIkida+lx+v3SzkvOzygHvhvndS+C102OgakyXnUO5B8PlC1/N3r9cllj7Nhn/uNravpqTS8e8iTpwrqxnDbom+LxHLtCcBATzQ/FtgHbJp5TWuPz49L+Pr01Y+XMnCMCNxp2GnhEEMcjU+iFiGHT6CGhsctDs/SgRS9oBMv0IOGWYZnPMjV8PteSgG2JGB5luHyTYGITOVgIlPd3h2XKN8o65qhFUHxjafO1KFd63/I26i567DYp77M4Tgmq4vPxvc96fmmIHw9xbHJdiMAtl/XUsX8WZSFJ54aL37Z3Zf2oz9jCtp/lLBA8MSS0LwRYHxqWYwiSxj3u5ntAOQ2yI3Bg6Cx6dAjS7lba8BoBMI0XvYjc+RrB8MnwL68hEPzSsE6QQAMZc4l4zTOHvAgg+DzfGZbPG8OSucdmXwhkY1jvc6WVSQQLBDcEE3m4ju1vH9YZsqM+GcaltzBvozeO+V1Xlv30NM/BPEE+Iz2D+yxjbnZADIOyzlAocs/y1LFPUBwO5XiVpK3QIG/aM/GBcvkctU3xK5mGSq1Mo2cs0LvQ34QQE6/zkDR1mSeuMxxFHu8ZdUWvTsgT5GNYLrCc6zdP8L6jyzccIJcb5R/nFOWV9+vX8zKor6tK64k7FAwpnsewYp6rmK9RuaxXHfvecCDp6Hy9z9izp5Y2qV26o7pnaXPJsMlwqCRJkvaA3qH+kSCSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmSJEmH4/9m+dVCjPY0/AAAAABJRU5ErkJggg==>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA4AAAAYCAYAAADKx8xXAAAAw0lEQVR4XmNgGAWjAAjKgLgBiE8DsR+SeDIQ/wdicSDmB+ITQPwPJskJxDlALMgA0TgHJgEEDxggGmHADJlvCcRKUPonEEfAJBggpiNrBIFvaHyGqwwQRYxQPguUvweuAgLWoPExTAf5C8RvRRLjAOJ0JD5W00EK3gKxJpIYKCxAYYICQBoPQNmsQLwKiJ8zQPwPAuFAfATKRgF/oRjkx0oGiG0gw2qg8rcYIKGKFTADsS8DxEYQABkiBsR2cBWjgIYAAHfTJlfm1fjyAAAAAElFTkSuQmCC>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEgAAAAYCAYAAABZY7uwAAAC3klEQVR4Xu2YTahNURTH/0II+YxEiUyUGCjlqygDEwYyUMwMGLyRQiZmBkqJlAnJQEpKBkoyeDIRI0WU1JsZyNAA+Vi/1lrv7LffvRT31c05v1qdtffa+5y911lr7XOv1NHR0TE8LDX5+Qe5OT66hUw3WWHyTe6M7dFGdpmcj/4Rk2k+pZ1ktPRimdz2pja0hZVyB7yuDQU/5GNaGUU75Ju/WxsKPsvHzKsNbQDHUINwVD8yBVvpIJyDk2bUhgCn/K5G/few8WN1Z8F6+RjqUOvIAs33UD8eysccrw1DChlRck3/EP175JP7pRd8MXlusqQ2DCmfqvZs/eXamfhAkz2ecKSfMvleG4KZJsvl9ymhzYcm9oR2jkNfGDoLp12T966hHmJjbcytDw3KweWizYdwPquEfr7v6vkTIHqoK72O943yyPpYG4I1JntDf1v0M35O6LfkG7losk9+v21h4+fLs9BZ5OPQgZeyJfRRNbXvusk6+X24pr3cJM7BSckRkxcmd6KNcy+ouf9Xk7mhj8OiD5q8kj/sfrSRQyaXov+q+temAya7Q2cBwBt9Fzo8kc/HGSfU1AGej/N2Rnu+fKNJGbGM+xA666EUkPLJUzUOWiRfC/cDnk0f409HH3W0PGym7OBZoObofxR9FEN+u63S5LB+L09n2Cz/8EwOm+wPfZN80wnjzhXtOoXK4st96mJMmvLcTG/svTJmoLzUxA2OxZWFs/kERyW8KVIabqiJCjZMUSWqcBJj8m3Tx7i1Jiejj5eQKcT1islZ+VyiJ517NK635eWAdRF9OIh0TygHA//5RBqRx0AknQl9qzxFE1IMWNiY/JMC2Mho6FmbVstTlDdOxLDoEfmGF5tsUJNCmVLMJdXvyQsuUUo6U19YF9A3S14HgbUT5Qm1aeAOgjxlypMKCOV+/QkbLu3opZ12OoFrHtFshLkl9UlHapcbRq/TPf/iqU/fjo6OqecXthaQ1Qokn98AAAAASUVORK5CYII=>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAAA8CAYAAADbhOb7AAAHzElEQVR4Xu3dXchlUxzH8b9QNGoMMgnNKJSXmLxN4WISNeU1lJdxY9wgeWkKjdIzuVCuvAyDSC7kJbkZIkk7FxpccDFjJkmZRJFEo7yzfq215qz5P3vt5+xzzoxnzvP91OpZe+1z1t7PQ82vtfZa2wwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABdtodyjG8cw+pQdoSyM5SVqW1FKIt2fwIAAABDOzuUf9PPcd1psa+rirZzQ3k+tQMAAGAEf1kMU5f7Ez29HcrfoRzgTwRbQvnENwIAAGBu94VyvMXApvqo1lns42B/IlHfa3wjAAAAuh0USpPqClsvDk71pu9v9I2F9aEs8Y0AAADo1lgMbfJPOh6VAttRvhEAAACjWx7KrcXxL6F8XRx754RyjW9MtLqUBQUAAAAT9qvFkFUWtdXkz7fR6tKu7y4OZa1vBAAAQN1NoZzm2t6yeiDL2lZ/ymEWp1RrnrPB1CsAAADmoNC11TdaDFVzBbYu+u7RvjE4z+pTqQAAAHButxisvgtlWdGuTW71/JrOaePb8tywZix+X28zyDaFckVxDADAgnGRxX8Yd4XybCg/WRw16XqGaBgXhPKYb5wQ7c2le1b/z4TyRyj32547679Z1EdxWyg3p7qm6PJzWeO+bkn3dYRvRKsDQ9lg8f/LcTfhBQBgv6RQpgCifww9TWeNG9g0+vKlb5wQ3bd/huljGwQ2/W4KceN4JZXSJAKb7qscNZKu1ZQAAGABe9liAGl78PsQGz+w7S15tOtI136xTebdlV0mEdi8pUZgAwAAFfnZoxof2O4KZafFUacy5Kn+dCg7QrkhlEUWp/xusT1H765NxyeHcqrFXfAPLc6L+lL/us6N7lwpT0+e4k8kV4byVHHsr/2qxZWNovt9KZRH0rGo33tt9k77PrBdau1/k1UW+3sglJUW70XTe/q7qK77EYU19alpaLXpvuV8i9dR0d9I31uVyrHpMwAAYMppBE1BoXHtNXoB97JUz1Op2gtLfk9t8oHFETC5zOLnMo2I6fhziyHkRHc+P5eW+30tlM2D03vQpqs5tOWiIJaprzJw+mvLtxavoZE50bspT091edhmh9YysJ2ZjkV/G9XzNK3+HtrW4jeLqxp17r10Tn2W79hUcG0bYdPzeXlrC/X3fSh3DE7Pogf9Ffq6yiW7Pw0AAOa9PoFtxmaPxD0eyjeprn4uTHWNTKlvadutXsc5rOR7yAGvCWVbqksefeqiUbJdNghty4tzPgSV1xYFqvL30nRqeV4PuXcFNtlQ1HWufDBefbXtJab7GiawKViWv78CJgAAWGAUBn70jYUv0k+FmqZoFwWOHCbWpbrKG7s/UQ9sZagpA5vCjR7I1xRjWYal7+dRLPEhyF9bQakpjkcJbJpO1eij7tv3r778PciwgU22WwxuJ4Sy2p3bW76ywX/PaSpsBwIA2C89avEfMk23tWnST42k+UBRBrZMo11quz4d9w1sCkfN4FSVPr/eN1psK+/T37O/9riBTZu3lr+f73+UwKbn/kq6p5lQ3nHtbXRtPwXqC1OiAADshzQ6VI5KZddZfMZMFMT81J4CxJZU1wPz2dUWp0ulb2BTiPmzOCcb3bHo83puznvBBtcWH5b8tccNbHq5+evunL6TRwVHCWyqe+q3vA4AAFhgNLqm6byfLU7viabgNM1Z0mKCvOpSozT6jr4rChT3pLqmUfU6Ia1kfCKdW5PO6aeO37e4cvLudPxg+rxstfhsmfo+w+JKTS9v61Hec17AkO9Jo0kKo7VrazXmDxaDnz6rZ++0zYmuvyp9Rp/Vd3Reiwk0+qXjTenzujcdi0YrtUpWCwU+tNiH+tI9aMQxL3TQ76m2Ty1eQxQU1Y9WkeZp6JLOLfWNAABg4dECAG1Doa0vanuZKVBocUEeEcs04qSgdJJrH5VWeGrFo3620b0el+oa0dI9+6nEfUV/E60CzdrefTkM/U5n+cakbZRuXylH/NpG/9oc7huccnQx+6xSH0VTqQMAAEyU3hKRp4P31WKDNn0DW96axYf60jiB7UmL/X9kMahrql6jl+XilKZSBwAAmChNC79r///Kxr6BTa8HU6AqV9J6owQ2TX2rX70v1lNoK581bCp1AACAqdQnsOn9s0ssBqtycYfXN7DlbWNqU+TqLz+rKE2lDgAAMJWGDWyaAlVgE4WrtlCWtZ2rBTYt9lB/bauFM23noqCYNZU6AADAVBo2sJWrWxWwuj7bJ7DNWOzvqKJtLk2lDgAAMJWGCWzanqTckFcBqymOvT6BTStk89Ypw2oqdQAAgKk0TGDTQ/8KVWVR0KrpE9i0mMBvXlxaHMpa19ZU6gAAAFNprsD2kM3ee26bdYesPoGtsdlv2SjpuTk951ZqKnUAAICp1BXYtP/ZZtcmetVZ1zRmn8CW32DhQ6Fow2K9z9VrKnUAAICpVAtsqywGKb1+q1wQoNd46ZVhOqe3T7QtFugT2GTGYn8rirZNVt+jrqnUAQAAplItsI2jb2ATvQJsg8W3G3Tt8SZNpQ4AADCV5ktg66Op1AEAAKYSgQ0AAGCeI7ABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADBP/AdB6QhWM3kP7QAAAABJRU5ErkJggg==>

[image8]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADUAAAAYCAYAAABa1LWYAAACS0lEQVR4Xu2XO2hUURCGf1FBUfCJGoKFoIiNFmLho7CwtTEWQuy1sLLQNmClnQ/Q1kaFiGAhWliINqKtkjaWJlgqiPiYjznHnYxnfYQs7JX94Cd7zszdO3PPzOyNNGLEiEGy2fT9D7r907sjLDeNmb7IEzhc1uio6XLZP2da5pd0h3oqLbbIbTPZMMyMy4N+mw2Bb3KfzpzWEXnA97Mh8FHuszYbhhWSoadIrh+1PDuTFAmR2IpsKJDI73puKCHYM3kzsEfuQ191gjok+L3qxxO5z9lsWASrTU9Na8LePtObsG75/BPH5AH3Kz34bHpl2pQNi4BTv5b2mKirwrrl89fwRY/kPdWCm10wfc2GQv3hjsODdetzhWAJusK1W8MaWj6t72rCKdEnrVG+V36C89lgrDM9Nu0s6xPyB3DFdNw0ZzpUbO/UC4hAP5TPsEN++pPq+UP02S73oeerz0U1JjUBnJTXMYE/LGt0ynS17N9Uu9cuqTc0Vppuyf14RzxvOl1s3CcmRfBxghIoZX9X3tuVlg8PnocAVFf0XxK46XPTLnkJRngrqf3B05zqmfTa9CKsgXfKOFU36FcfEouJDmQK86WUQIVpVV+f4g1JEBunDwRGqdJD98oeQ2jKdKOsOc3s86wIOLXZ8nlJeSkvGdhouiNPKt+QVyv6bzqs95uumybK3ifTAXkJA+WbfSg3ThnYYz0Q6uTLxNGMT5yM9F+edPjnvbwGrl0vP9WDC03dg4fy3rRbniz//lDSnYYEHsjLkr/bFpr/Y34ARKZ4Lmd6F7AAAAAASUVORK5CYII=>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAAA+CAYAAACWTEfwAAAHeElEQVR4Xu3dTahVVRjG8TesSMw+7JsKbxGBZJQENVFEqEFEDiqisEHQoAbSoKLQWUiDBkGEEURgEVGEDRoEIRKHRkINGhhFNbCIAsOCsDDFaj2sve5Z571rf5x77j3uc/3/YHH2XvvcszdOfFgf7zYDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAWeM833EWWOU7AAAA+uyF0C70nSvc/aFd4zsBAAD6qi6wPRTaf6F9XB2n9lHVf93wq1NxuQ2f6fHqWO2R0I5W/V0R2AAAwEypC2yiEHSB7wze8R1j+CK0J7LzudD+DW111lfnWqsPZj/6jgYENgAAMFPqApv6TmXnCkt3V8eDrH8cb4V2se+s/Oo7CvSseWDblx1/kh23IbABAICZUhfYtoe2Pzs/ZOXRtnF85TsyClFrfadzxIbBTpsljg0vjYXABgAAZkpdYBtYXBv2U2gnLU5btjntO5ymacsuIUqja8ctPpOOXxu9PG9raH+HtjG0LRanYXNd7gUAANAbdYFNgejc7DyfctyWHSdPWQxJTSYJbGn92o3V+ebQNlTH60JbUx0/H9qb1XHip1vb7gUAANArpcCmqcl8rdg5Fndpyj0WR65yuv6YNQcyabreFqJ22+gzrc+OD1efV1p5JPAld952LwAAgF4pBbYHrH435gnfEbxffQ7yzoJJAtvPtnCkTFReZG91rFFAH85K2u4FAAB6yoeWabvMd0xJKbBpzVe+Q1Q0iqa1YztdvwLTMxZron3urnmTBDYFSB/GdG/1p5Igf4V2x/ByrbZ7AQCAM+ASi/9Bp+YDyp0W1z61edZiwFmsudAOWgw++i3RbkeVy9Dnd1XfNOWBTcFLAaipeQey47Z/m8UEtj9t4TPkTbtXk4HFtW25W9y51N0LAACcQVrbpHVXGjVSaMjLUygo/Zad19ECfAWExRSNnbP4t29kfRoV+qHqv7Tq0/qwV+e/MR2lEbau9oR2RXb+sLWX5lhOqvGmnaoaDZS50O6bvzpEYAMAoMcUjp50fZ/awkX0JQOLf6/PcaT1YNrJ6Om1Sn7USovm892Zy22SwNZXCmRqdQhsAAD0lMpBKByl3Y6JD0wl11sMNlr03jSt56U1VqoFVqLQ4GuEqbaYXye2nFZiYGtDYAMAoKe0YN2Hs9usWwD7vfpUuNLC9q7+Ce0P35lRoNvh+vRM2hE5LZqGTVOIZ4tbrdu7SwEAwJRpdOxr16fRpS5r0jZVnyob4UNfnRusPAXbRqNdbffQjsy2puAHAAAwU0rhSWGtbWfjruxYLy9vC1OJfrc0BdtF13tM4l3afAMAAD1wr8UQ5Kf+2gKbpgufs+Go1csWf6fLS9BTYKujjQV178Js+jsAAIAVQe+SzEPPwMprzxSYmqZEv3fnKsqq373K9ZekKVG9A7NE9dh8gJS2KVEFPdVxa2tvV98HAADoJYWzVFtNddYUgDRa5qlgrd+lmXxmcVNATjsL9Vt5NX2FPvWp1pv3jZU3Heyz0Xdh5rSb9ZjvBAAAWGlU++xwaE9bDFNbRy/P09SmH81SyQf1pZZKXugtBHn/oOq/3WLttLq6X3rPpr5/NLRfLO5KXTXyjVGaSq2bKl0OervBGt+5wmmKvMsoKQAA6AmV0Jj0P2+NxC3VrkyNyKli/7TU1WHLw2mpdXln51LSv69/Bt+6og4bAAAz5iYb/+0F3sB3LJIK9La9QH2p1QU2UQjStLGnKefF7H590HdkrrY4fd2mFMwUcDXK2RWBDQCAGaRNCgpui3GzxWnRpaAXnZc2IiyntsBW2hHbtFGjSVOR4i4hShs4SoFNmn7b63IvAADQQ3oTwpn0ik0/rEldYFPfqexcYSmNtg2y/nE0haouIcqXStmXHauocVdd7gUAANAbdYFte2j7s/NDVh5tG8ekge2IxTdWiKZPF7ubtsu9AAAAeqMusA0s7mxVTbeT1m2NWD7iVTJpYNPo2nGLz6Tjpt20d4X2bWgfWNwUcn52rcu9AAAAeqMusCkQqUhvkk85bsuOEy38r1tflkwS2NL6NdWpk82hbaiO19mwNIlKpihcXlSdiw+bbfcCAADolVJgW2uj4Utr69KuUBUg3phdS1RoWCVSmrQFNv8cud02+kzrs2PV3EtO28KyKH59G4ENAADMlFJgU+HhutGyE74j2GLxNwau3/Ov+spp00fTGjmFwbR+Lafpzr3V8U6La+08X4KEwAYAAGZKKbDpdV35DlHRKJvWjikUea9Xn++N9C60KbRdvjOYC+2A73QUIP1OXoU19a+uzjWCp7cYtCGwAQCAmZIHNr2mSgGoqXkvWqxjp/alu1aitWWatjxo8W/0m4+OfGOUatP5Z8hbPqKmETj/BoZSqRQCGwAAmCmlEbauNLKlNW3JDisHpGnR6N8e1/ehOxcCGwAAmCmTBLa+UiBTq0NgAwAAM2UlBrY2BDYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALBy/A9k2pRFwH+pVwAAAABJRU5ErkJggg==>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAZCAYAAADuWXTMAAAA60lEQVR4XmNgGAXGQPyfSMwP1QMHIkAcAsRLGSAK9kH5MJwMxLuhctxQPSiAF4gPA/EnINZHk4OBu+gCMKAJxG+B+DQQC6LJwcABdAEYmMQAcVY0kpgMEF9B4l9CYqOAAwwQzchOjmBA1WyNxEYBsNCcDcSzgPg+lJ+OrAgXgGl+BMVfgPg3ENsgK8IGlBggGq+iia8BYhYkvi4SGw5aGTCdyMgAiX8YiGHAkkA4gHgrA34nCgPxKXRBEHAB4n8MmE5EBu+BeDm6IAhUMUCcXI4uAQTMQOzBADEcRMOBJANmoseFc6B6RsHQAgBVKUGiQLl9DAAAAABJRU5ErkJggg==>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC0AAAAYCAYAAABurXSEAAACCklEQVR4Xu2WPUicQRCGRzREIRKDooQoFlZBUMEfEA2kCJLGVBaB2FloYW2CraRIJbFJI4iFCGJjIdjbxdaQKmAhpBAbwYCIifM4s2Zv77tL4ZH7kHvg5XZn5vZmd2d3T6RGjftNm+rPP7R+G50T6lVPVZdiCY55H71UfXL7vKrOvpIfwqpm0S7m+546qskzsaS+pY6I32IxuVntcbGEtlNHxLlYzKPUUS1Ilpom+VKE8slN0iRM4g2pwyHRcjVfFUhmNjVGPBeLoa5zQTiE3Nel2BOLmUvs7FDKf9mNV2I/VKo04EL1VdWa2E+TPqQxFadRtSvZKwZcbwuqq9QhVjIrUZ9Hivs8piNq81gRA5wRJpd1fZJT/L0iWGXqNOuq6xPbgZPU4ZAwiQemVU9UW95/LVZ6jP/BbYx3JJbse9WZ22FItapqcv9k5LsB45TqUGygHe+jt6rPbv8i2bVOcgeqZu8Tg21ULBng+e8Xm8QDtzHmkrcXxe5+YNUpvy7vd6t6vV0x3knxgWNLeebZ3gArF3aDkmCiTA5+qta8TRzjTag63VZx+PF9b8/456bqWDUofw91vP1vxCYLTIwJMlF2lVILqx7oSfp35ofY39UR1ePItqFaDkFSuBsfxSYEHEpiH6peiJXVL/fBsGog6leMFik8/bTT646YQPr8Exv7gcmUvTlq1MjgGmSyZGt9JOtpAAAAAElFTkSuQmCC>

[image12]: <data:image/png;base64,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>

[image13]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAsAAAAYCAYAAAAs7gcTAAAApklEQVR4XmNgGNqABYgLgfg/EP9Ck8MJ7jJANIijS2ADlkD8E4iXo0vgAuUMENOJAjoMEMWc6BK4AEgxyAaiAMjNILeD/EAQ3GKAmN6KLoEMNBkgwafAAFH8D0UWDTwC4vdQNkghSAMjQhoCrBggihIYEJINDBDFEVA+GLhABWchCzJAgm4HEL9FFgSZuAeI+ZEFoSCdAS2C3JE5WEAAEAuhC45sAADefyA5dFJUAwAAAABJRU5ErkJggg==>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAsAAAAXCAYAAADduLXGAAAAwUlEQVR4XmNgGNrgExD/h+KraHJYgSYDRHERugQ2MAmIHwCxNJo4BhAE4tNAvAaIWdDkMEA0A8QJNkhi+UC8DIhZkcTAAGQqSDEHEDMC8WEgNgHi10A8A0kdGMBCA6QwB4iFgHgrVAzDwyDBb0C8Coj50eRQACzI6oF4F5RdjKICCYA895sB4jlmIG4H4vcoKpAAyDPIQebLAHESCCgBsSWUDQboQQaSfALE8kB8EUkcDECKhZH4oBCBeRin20c2AACyQSdsCIBWkgAAAABJRU5ErkJggg==>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAB4AAAAXCAYAAAAcP/9qAAABGklEQVR4XmNgGAWjgDLQCcT/gfgXEBcDMR+qNG2AExCfAmIZILZlgDjgNYoKIGBFF6AQpDNALNqFJLYGKtaKJAYG84H4HxBbATEzmhypQJ8B4rvpSGJbGSAWVyGJwYEwAyQ+rjNQHgrojn/CALHYGE0cA0QA8TcGSAKhBIA8kwPEf4G4DE0OJ4AlEFA0kAPKGSC+BGFrNDmiwVwGiKtBjiEHHGKAOMAEXYIQoNRiUwZI1IEsJwowAvF2BkjW0EOTwwVAcXoDiLmRxECJ6isDERZTkrhg8VqEJOYLFfuJJIYCQK7MB+JXUDY54CUDxBJQqQUDsOJzFZIYGIDi8A8DdQoQkH5YFgKZe5oBYulyZEUgQGlhMQpGweABAPnMO/pAHiDxAAAAAElFTkSuQmCC>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADcAAAAXCAYAAACvd9dwAAACVElEQVR4Xu2WT0hVQRjFT2RQkFYI/SGDikAKd1LRzq1Ym9wIQYvatHWTELpy1TaIQBRq4SJxUatciguNCIIoBNGFEkRFhIJQRuk577vz7ty5f3x/aiP3Bwfe/ea7c+fMfDPzgJKSkr1EH/WH2qYeUm3J5lz2I373C/U42VzhLazPo9FzO/WcGqtm/EcOUX+pi9Qpap36lsjI5wn1k+qiBqhl6nYiA9iETZqvH9QVP6leDoaBDDQofajbi7VQ09SoF8viHPUgDJJf1D7vWebeU2vUCnXVa6sLdfoKViaXgrYsZmEzeTiI34StZhE3qCXqQhD/Dpsgh8wptylU/+9gdX8maMvjE7LNaTCKF6HVVo7KWN8WmtxwUpoyN0x9pe6EDTXwG42bE1o5t48+wvafM+qQuQlYlegQ2aKuI1m6KbQ6WqVFpDusFTewPHN+eWVxknqJuB8dKCEyp/3m0EGibdPvxSrIxAJs6TuDtkbYzVzRoaSZ1yAnYVeH60sqXBXEeQn00r80t4Fic3no+pihrmXE9d5Q9Kzxqq/w3nTmjgXxCjImgzK62ywVoX3SiDndiToVswY3RT2LfvfA+nldbTWcudYgnqKZA0UXrz5yPog/QnJAB6KYu16U/5k6Xc2IuRVJ9MJKdzBurpBZlnnoX8EqzGg9HKc+wEw6jsCM3fNid2GD0dUhdNCMU/eRrpw5WL/iBNI5+vulvt54sZrwL/Fa96Te0fH8lBqBlbtKy6cDNqBw8uaj+AvYJOn35USGTbz6V46knLN+QiMUnXQlJSUle5MdZ2yPRbwbgVAAAAAASUVORK5CYII=>

[image17]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADgAAAAWCAYAAACVIF9YAAACjUlEQVR4Xu2WS6iNURTHl1DklceAIgYemZAoJRkoMqGQGJhTjBkZSAaYSKKUbpihDD0yuGEgBiZCZEAijAwYeP9/d+197W9953HvOffeUudX/86319rn22vtx9qfWY8eY8E4aam0JToCs6SJ0fg/0Cf9lJ5Ky4Ov5Io0NRpHi1PSLvs3o0ukR9LqwR7OPOmj9Ed6IG2ougf6P5ZmpvY76bd02vy/86Vr5v/fm/qMCZfMB836bvXts0j6WrTxs1qHChvvQZnD0vWiDfulV8FWIQ48EhDUM+mt9FCaXXUPnKuz5gmVrJfeF+1+qyeIreSDtDPYajAQS78uOjqEoFqdh23mK7s12Kcl+6TUPm7VBInzTHpmklhttumQmSJ9kT6l504hqOnSUemCtKDqHtzCMUEmBfvK1GZ3/TJfIRK6k/oAZ5qzOGx46R7pm3Qi+IYKCdxKvyRI0JTxDOeoVYJlMbptfobvS2uSjWRJMG79YcOLbprP3IrgawVnZXLRJmACP5ba/andLMFojzw3jw3mSnfNK+x566KmUOoZvBNy4JxzaLdF43VSsltaVbTpT8ECPgZeF762dLKCN8wHZZtncuB5gval52YJzgn2zNWkDP3ovyO1J1j9GmnKRvN93hcdbXhjPijbNJOrY15BroMf5omWcHnTj0AbwTZcXLRz/3Kiyqpbg8pJBe2mih6UXgYbXxkEUn5tcEFzR5aw6nySNYJzFs8XMZaTyURSjGpcNP/eG6l7kIPPwMzmC/MqOL7SwyHoJ9IR86rNFZWLR8laaVM0JphMig6w2yg0FbZLl6Vl0dElBMUVcc6a7waSOWneb7PVVwhmSPesceLA1fFZOmC++gur7h49evQYJf4CH4qLSHodH7MAAAAASUVORK5CYII=>