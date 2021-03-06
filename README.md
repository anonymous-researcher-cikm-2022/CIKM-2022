# Easy Begun is Half Done: A Curriculum Learning Approach for Spatial-temporal Graph Modeling

## Descriptions
Source code of the CIKM'22: Easy Begun is Half Done: A Curriculum Learning Approach for
Spatial-temporal Graph Modeling

Currently only the partial codes are uploaded. We will update all the codes upon the acceptance of this paper.
## Requirements
* `Python==3.8`
* `pytorch==1.7.1`
* `torch-summary (>= 1.4.5)`


#### Running
  `python main.py`

#### Dataset
We provide sample data under data/.

The project structure is organized as follows:
```
├── data
│   └── METRLA 
│       ├── metr-la.h5    # signal observation
│       ├── W_metrla.csv  # adj maxtrix
├── models
│   ├──  STGCN.py # STGCN framework
│   ├──  Param.py # hyper parameter 
├── save
├── main.py
├── README.md
└── utils
    ├── Metrics.py # evaluation metrics 
    ├── Utils.py  
```
