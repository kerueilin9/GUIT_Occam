```
deactivate && conda activate agentoccam
```

```
. .\buildEnv.ps1  
 
python browser_env/auto_login.py --site_list timeoff
python browser_env/auto_login.py --site_list keystonejs  
python browser_env/auto_login.py --site_list nodebb  
python browser_env/auto_login.py --site_list postmill  
python browser_env/auto_login.py --site_list onestopshop  
python eval_webarena.py --config config_files/timeoff_config.yml  
python eval_webarena.py --config config_files/keystonejs_config.yml  
python eval_webarena.py --config config_files/nodebb_config.yml  
python eval_webarena.py --config config_files/postmill_config.yml 
python eval_webarena.py --config config_files/onestopshop_config.yml 
```

```
# LLM Actor prompt
# logger for debugging LLM evaluation responses

```