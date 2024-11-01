from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from .models import Profile
from django import forms
from django.http import HttpResponseRedirect 

class CsvImportForm(forms.Form):
    csv_upload = forms.FileField() 

class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'creative', 'display_name', 'discord', 'dwd', 'league')
     
    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-profile-csv/', self.upload_csv)]
        return new_urls + urls

    def upload_csv(self, request):

        if request.method == 'POST':
            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info )

            file_data = csv_file.read().decode('utf-8')
            csv_data = file_data.split('\n')

            for x in csv_data:
                fields = x.split(',')
                created = Profile.objects.update_or_create(
                    discord = fields[0],
                    )
            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)


admin.site.register(Profile, ProfileAdmin)
