# django-ec2
project template for deploying django to an EC2 Ubuntu 14.04 AMI with fabric 
client - nginx - uwsgi - django


### Deployment

    # update fabric hash values in local_settings
    cd project
    fab apt_update
    fab install_server_requirements
    fab make_env
    fab pull
    fab install_project_requirements
    cd ..
    scp -i ~/.ec2/rcm-west-key-pair.pem db.sqlite3 ubuntu@54.67.47.65:/home/ubuntu/client_portal
    cd project
    fab collectstatic
    # update deploy templates
    fab upload_templates

