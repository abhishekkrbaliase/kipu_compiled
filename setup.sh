conda create -n scrapper python==3.7 -y
conda activate scrapper
pip3 install -U selenium==4.7.2
pip3 install webdriver-manager==3.8.5
pip3 install chromedriver-py==116.0.5845.96
conda install pandas==1.3.5 -y
pip3 install %cd%\lib\kipuUtils-0.0.2-py3-none-any.whl
